# SPDX-License-Identifier: Apache-2.0

"""Policy-gated project writes and fixed-profile verification execution."""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import os
import signal
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from eis.core.approvals import ApprovalError, ApprovalRecord, content_hash
from eis.core.audit_chain import AuditIntegrityError, AuditIntegrityGate
from eis.core.policy_service import PolicyService
from eis.core.sandbox import (
    SandboxBackend,
    SandboxError,
    SandboxRequest,
    SandboxResult,
    declared_env,
    validate_env_allowlist,
)
from eis.policy import (
    Action,
    PolicyContext,
    PolicyError,
    PolicyRequest,
    Principal,
    Resource,
)
from eis.storage.approvals import ApprovalStore

MAX_WRITE_BYTES = 1_000_000
MAX_DIFF_BYTES = 2_000_000
DEFAULT_OUTPUT_CAP = 2_097_152
_RESERVED_TOP_LEVEL = frozenset({".git", ".eis", ".ssh", ".aws", ".gnupg", "keys", "secrets"})


class ExecutionDenied(PermissionError):
    """Raised when policy, approval, isolation or path checks deny an operation."""


@dataclass(frozen=True)
class CommandProfile:
    """Application-defined command; caller/model supplies no executable or arguments."""

    profile_id: str
    argv: tuple[str, ...]
    timeout_seconds: int = 900
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP
    minimum_tier: Literal["T1", "T2"] = "T1"
    needs_writable_workspace: bool = False
    env_allowlist: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.profile_id or len(self.profile_id) > 100:
            raise ValueError("profile_id must contain 1 to 100 characters")
        if not self.argv or not Path(self.argv[0]).is_absolute():
            raise ValueError("command profiles require an absolute executable path")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be 1..3600")
        if not 1024 <= self.output_cap_bytes <= 16_777_216:
            raise ValueError("output cap must be 1 KiB..16 MiB")
        if any("\x00" in arg for arg in self.argv):
            raise ValueError("command arguments must not contain NUL")
        if self.needs_writable_workspace and self.minimum_tier != "T2":
            raise ValueError("a writable workspace requires T2 isolation")
        validate_env_allowlist(self.env_allowlist)


@dataclass(frozen=True)
class ExecutionResult:
    run_id: str
    status: Literal["success", "failed", "refused", "unavailable"]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    truncated: bool
    timed_out: bool
    error_code: str | None
    approval_id: str | None = None
    backup_id: str | None = None


class ExecutionGateway:
    """The only supported write/verification execution boundary in this slice."""

    def __init__(
        self,
        *,
        project_roots: Mapping[str, Path],
        trusted_projects: frozenset[str],
        backup_dir: Path,
        policy: PolicyService,
        approvals: ApprovalStore,
        integrity: AuditIntegrityGate,
        command_profiles: Mapping[str, CommandProfile] | None = None,
        sandbox: SandboxBackend | None = None,
    ) -> None:
        self._roots = dict(project_roots)
        self._trusted = trusted_projects
        self._backup_dir = backup_dir
        self._policy = policy
        self._approvals = approvals
        self._integrity = integrity
        self._profiles = dict(command_profiles or {})
        self._sandbox = sandbox
        if not set(self._trusted) <= set(self._roots):
            raise ValueError("trusted project IDs must be registered roots")
        for project_id in self._roots:
            Resource("project", identifier=project_id)
        backup_path = self._backup_dir.resolve(strict=False)
        for root in self._roots.values():
            resolved_root = root.resolve(strict=False)
            if backup_path == resolved_root or resolved_root in backup_path.parents:
                raise ValueError("backup directory must be outside every project root")

    def propose_write(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        relative_path: str,
        new_content: str,
        on_behalf_of: str,
        summary: str,
    ) -> ApprovalRecord:
        """Authorize an agent proposal and a user write before creating an approval."""
        if len(new_content.encode("utf-8")) > MAX_WRITE_BYTES or "\x00" in new_content:
            raise ExecutionDenied("proposed content is invalid or exceeds the size limit")
        try:
            actor = Principal.parse(on_behalf_of)
        except PolicyError as exc:
            raise ExecutionDenied("invalid proposing principal") from exc
        if actor.kind != "agent":
            raise ExecutionDenied("only an agent proposal can request a delegated write")
        target = self._resolve_target(project_id, relative_path)
        root = self._root(project_id)
        canonical_path = relative_path.replace("\\", "/")
        resource = Resource.for_path(project_id, canonical_path)

        proposal = self._policy.evaluate(
            PolicyRequest(
                principal=actor,
                action=Action("project.propose"),
                resource=Resource("project", identifier=project_id),
                context=PolicyContext(source="agent", task_id=task_id),
            )
        )
        if proposal.effect != "allow":
            raise ExecutionDenied("project proposal denied by policy")
        read_decision = self._policy.evaluate(
            PolicyRequest(
                principal=Principal("user"),
                action=Action("project.read"),
                resource=resource,
                context=PolicyContext(source="cli"),
            )
        )
        if read_decision.effect != "allow":
            raise ExecutionDenied("user read denied by policy")
        write_decision = self._policy.evaluate(
            PolicyRequest(
                principal=Principal("user"),
                action=Action("project.write"),
                resource=resource,
                context=PolicyContext(source="cli"),
            )
        )
        if write_decision.effect != "allow":
            raise ExecutionDenied("user write denied by policy")

        old_bytes = target.read_bytes() if target.exists() else None
        if old_bytes is not None:
            try:
                old_text = old_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ExecutionDenied("binary files cannot be modified by this gateway") from exc
        else:
            old_text = ""
        new_bytes = new_content.encode("utf-8")

        diff = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            )
        )
        if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
            raise ExecutionDenied("proposed diff exceeds the size limit")
        return self._approvals.request_write(
            decision=write_decision,
            task_id=task_id,
            on_behalf_of=on_behalf_of,
            resource=resource.canonical,
            project_root_hash=_root_hash(root),
            expected_sha256=content_hash(old_bytes) if old_bytes is not None else None,
            new_sha256=content_hash(new_bytes),
            diff_text=diff,
            new_content=new_content,
            summary=summary,
        )

    def grant(self, approval_id: str, *, confirmed_digest: str) -> ApprovalRecord:
        """Record the human's explicit approval of the exact displayed digest."""
        self._integrity.require_intact()
        return self._approvals.decide(
            approval_id,
            grant=True,
            confirmed_digest=confirmed_digest,
        )

    def reject(self, approval_id: str, *, confirmed_digest: str, reason: str) -> ApprovalRecord:
        self._integrity.require_intact()
        return self._approvals.decide(
            approval_id,
            grant=False,
            confirmed_digest=confirmed_digest,
            reason=reason,
        )

    def execute_write(self, approval_id: str, *, confirmed_digest: str) -> ExecutionResult:
        """Consume a single-use approval, back up the target, then atomically replace it."""
        record = self._approvals.get(approval_id)
        if record is None:
            raise ApprovalError("approval not found")
        if record.status != "approved":
            raise ApprovalError("approval is not approved or has already been used")
        if record.expires_at_ms <= _now_ms():
            raise ApprovalError("approval has expired")
        if record.task_id is not None and self._approvals.task_is_terminal(record.task_id):
            raise ApprovalError("approval task is terminal")
        project_id, relative_path = _parse_path_resource(record.resource)
        root = self._root(project_id)
        if _root_hash(root) != record.project_root_hash:
            raise ExecutionDenied("registered project root changed since approval")
        target = self._resolve_target(project_id, relative_path)
        read_decision = self._policy.evaluate(
            PolicyRequest(
                principal=Principal("user"),
                action=Action("project.read"),
                resource=Resource.for_path(project_id, relative_path),
                context=PolicyContext(source="cli"),
            )
        )
        if read_decision.effect != "allow":
            raise ExecutionDenied("user read denied by policy")
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=Principal("user"),
                action=Action("project.write"),
                resource=Resource.for_path(project_id, relative_path),
                context=PolicyContext(source="cli"),
            )
        )
        if decision.effect != "allow" or decision.policy_version != record.policy_version:
            raise ExecutionDenied("write policy changed or no longer allows this action")
        if decision.decision_id != record.policy_decision_id:
            raise ExecutionDenied("write policy decision changed since approval")
        current = target.read_bytes() if target.exists() else None
        current_hash = content_hash(current) if current is not None else None
        if current_hash != record.expected_sha256:
            raise ExecutionDenied("target changed since approval; request a new approval")
        self._integrity.require_intact()

        consumed = self._approvals.consume(approval_id, confirmed_digest=confirmed_digest)
        started = _now_ms()
        run_id = f"run_{uuid.uuid4().hex}"
        backup_id: str | None = None
        try:
            self._approvals.record_execution_start(
                run_id=run_id,
                approval_id=approval_id,
                action="project.write",
                resource=record.resource,
                profile_id=None,
                started_at_ms=started,
            )
            if current is not None:
                backup_id = self._backup(current)
            self._atomic_replace(target, consumed.new_content.encode("utf-8"), current)
            completed = _now_ms()
            self._approvals.record_execution(
                run_id=run_id,
                approval_id=approval_id,
                action="project.write",
                resource=record.resource,
                profile_id=None,
                status="success",
                started_at_ms=started,
                completed_at_ms=completed,
                output_sha256=record.new_sha256,
                backup_id=backup_id,
            )
            return ExecutionResult(
                run_id=run_id,
                status="success",
                exit_code=0,
                stdout=b"",
                stderr=b"",
                truncated=False,
                timed_out=False,
                error_code=None,
                approval_id=approval_id,
                backup_id=backup_id,
            )
        except Exception as exc:
            completed = _now_ms()
            with contextlib.suppress(Exception):
                self._approvals.record_execution(
                    run_id=run_id,
                    approval_id=approval_id,
                    action="project.write",
                    resource=record.resource,
                    profile_id=None,
                    status="failed",
                    started_at_ms=started,
                    completed_at_ms=completed,
                    error_code=type(exc).__name__,
                    backup_id=backup_id,
                )
            if isinstance(exc, (ApprovalError, AuditIntegrityError, ExecutionDenied)):
                raise
            raise ExecutionDenied("approved write failed; approval remains consumed") from exc

    def run_profile(self, project_id: str, profile_id: str, *, principal: str) -> ExecutionResult:
        """Run a fixed, application-defined verification profile with T1 limits."""
        profile = self._profiles.get(profile_id)
        run_id = f"run_{uuid.uuid4().hex}"
        started = _now_ms()
        if profile is None:
            result = ExecutionResult(
                run_id, "unavailable", None, b"", b"", False, False, "profile_unavailable"
            )
            self._record_profile_result(run_id, project_id, profile_id, started, result)
            return result
        # An untrusted project always routes to T2; a T1 profile is never a
        # security boundary for content authored by a hostile repository.
        requires_t2 = profile.minimum_tier == "T2" or project_id not in self._trusted
        if requires_t2 and (self._sandbox is None or not self._sandbox.capabilities.sufficient):
            result = ExecutionResult(
                run_id, "refused", None, b"", b"", False, False, "isolation_unavailable"
            )
            self._record_profile_result(run_id, project_id, profile_id, started, result)
            return result
        root = self._root(project_id)
        try:
            actor = Principal.parse(principal)
        except PolicyError:
            result = ExecutionResult(
                run_id, "refused", None, b"", b"", False, False, "invalid_principal"
            )
            self._record_profile_result(run_id, project_id, profile_id, started, result)
            return result
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=actor,
                action=Action("project.verify"),
                resource=Resource("project", identifier=project_id),
                context=PolicyContext(source="agent"),
            )
        )
        if decision.effect != "allow":
            result = ExecutionResult(
                run_id, "refused", None, b"", b"", False, False, "policy_denied"
            )
            self._record_profile_result(run_id, project_id, profile_id, started, result)
            return result
        self._integrity.require_intact()
        resource = f"project:{project_id}"
        self._approvals.record_execution_start(
            run_id=run_id,
            approval_id=None,
            action="project.verify",
            resource=resource,
            profile_id=profile_id,
            started_at_ms=started,
        )
        try:
            if requires_t2:
                assert self._sandbox is not None
                request = SandboxRequest(
                    run_id=run_id,
                    profile_id=profile_id,
                    argv=profile.argv,
                    project_root=root,
                    env=declared_env(profile),
                    timeout_seconds=profile.timeout_seconds,
                    output_cap_bytes=profile.output_cap_bytes,
                    writable_copy=profile.needs_writable_workspace,
                )
                try:
                    result = _sandbox_result(run_id, self._sandbox.run(request))
                except (OSError, SandboxError):
                    result = ExecutionResult(
                        run_id, "unavailable", None, b"", b"", False, False, "isolation_unavailable"
                    )
            else:
                result = _run_limited(profile, root, run_id=run_id)
        except FileNotFoundError:
            result = ExecutionResult(
                run_id, "unavailable", None, b"", b"", False, False, "executable_unavailable"
            )
        except OSError:
            result = ExecutionResult(
                run_id, "unavailable", None, b"", b"", False, False, "executable_unavailable"
            )
        self._record_profile_result(run_id, project_id, profile_id, started, result)
        return result

    def _record_profile_result(
        self,
        run_id: str,
        project_id: str,
        profile_id: str,
        started_at_ms: int,
        result: ExecutionResult,
    ) -> None:
        self._approvals.record_execution(
            run_id=run_id,
            approval_id=None,
            action="project.verify",
            resource=f"project:{project_id}",
            profile_id=profile_id,
            status=result.status,
            started_at_ms=started_at_ms,
            completed_at_ms=_now_ms(),
            exit_code=result.exit_code,
            output_sha256=hashlib.sha256(result.stdout + result.stderr).hexdigest(),
            error_code=result.error_code,
        )

    def _root(self, project_id: str) -> Path:
        root = self._roots.get(project_id)
        if root is None:
            raise ExecutionDenied("project root is not registered")
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise ExecutionDenied("registered project root is not a directory")
        return resolved

    def _resolve_target(self, project_id: str, relative_path: str) -> Path:
        root = self._root(project_id)
        if not relative_path or "\\" in relative_path or "\x00" in relative_path:
            raise ExecutionDenied("target must be a relative POSIX path")
        parts = relative_path.split("/")
        if any(part in {"", ".", ".."} for part in parts) or relative_path.startswith("/"):
            raise ExecutionDenied("target path is not normalized")
        if parts[0].casefold() in _RESERVED_TOP_LEVEL:
            raise ExecutionDenied("target path is reserved")
        name = parts[-1].casefold()
        if name == ".env" or name.startswith(".env."):
            raise ExecutionDenied("target path is reserved")
        candidate = root.joinpath(*parts)
        cursor = root
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
                raise ExecutionDenied("symlink and junction paths are not writable")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ExecutionDenied("target escapes the registered project root") from exc
        if candidate.exists() and candidate.is_dir():
            raise ExecutionDenied("target must be a regular file")
        if not candidate.parent.is_dir():
            raise ExecutionDenied("target parent directory must already exist")
        return candidate

    def _backup(self, original: bytes) -> str:
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        backup_id = f"bkp_{uuid.uuid4().hex}"
        path = self._backup_dir / f"{backup_id}.bin"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        return backup_id

    def _atomic_replace(self, target: Path, content: bytes, expected: bytes | None) -> None:
        current = target.read_bytes() if target.exists() else None
        if current != expected:
            raise ExecutionDenied("target changed during approval consumption")
        descriptor, temp_name = tempfile.mkstemp(prefix=".eis-write-", dir=target.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            current = target.read_bytes() if target.exists() else None
            if current != expected:
                raise ExecutionDenied("target changed before atomic replacement")
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()


def _sandbox_result(run_id: str, result: SandboxResult) -> ExecutionResult:
    return ExecutionResult(
        run_id=run_id,
        status=result.status,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        truncated=result.truncated,
        timed_out=result.timed_out,
        error_code=result.error_code,
    )


def _parse_path_resource(resource: str) -> tuple[str, str]:
    kind, separator, value = resource.partition(":")
    project_id, path_separator, relative_path = value.partition("/")
    if kind != "path" or not separator or not path_separator:
        raise ApprovalError("approval resource is not a project path")
    return project_id, relative_path


def _root_hash(root: Path) -> str:
    resolved = os.path.normcase(str(root.resolve(strict=True)))
    return hashlib.sha256(os.fsencode(resolved)).hexdigest()


def _run_limited(profile: CommandProfile, cwd: Path, *, run_id: str) -> ExecutionResult:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if os.name == "nt":
        process: subprocess.Popen[bytes] = subprocess.Popen(
            profile.argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    else:
        process = subprocess.Popen(
            profile.argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
    stdout = bytearray()
    stderr = bytearray()
    total_bytes = 0
    lock = threading.Lock()
    overflow = threading.Event()

    def drain(stream: object, captured: bytearray) -> None:
        nonlocal total_bytes
        pipe = stream
        if pipe is None:
            return
        while True:
            chunk = pipe.read(8192)  # type: ignore[attr-defined]
            if not chunk:
                return
            with lock:
                remaining = max(0, profile.output_cap_bytes - total_bytes)
                if remaining > 0:
                    captured.extend(chunk[:remaining])
                    total_bytes += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    overflow.set()
            if overflow.is_set() and process.poll() is None:
                _terminate_process(process)

    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=profile.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for thread in threads:
        thread.join(timeout=2)
    truncated = overflow.is_set()
    error = "timeout" if timed_out else "output_truncated" if truncated else None
    status: Literal["success", "failed"] = (
        "success" if process.returncode == 0 and not timed_out and not truncated else "failed"
    )
    if status == "failed" and error is None:
        error = "nonzero_exit"
    return ExecutionResult(
        run_id=run_id,
        status=status,
        exit_code=process.returncode,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        truncated=truncated,
        timed_out=timed_out,
        error_code=error,
    )


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            break_signal = getattr(signal, "CTRL_BREAK_EVENT", None)
            if break_signal is not None:
                process.send_signal(break_signal)
            else:
                process.terminate()
        else:
            kill_group = getattr(os, "killpg", None)
            kill_signal = getattr(signal, "SIGKILL", None)
            if kill_group is not None and kill_signal is not None:
                kill_group(process.pid, kill_signal)
            else:
                process.terminate()
    except (OSError, ValueError):
        process.terminate()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
