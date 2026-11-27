# SPDX-License-Identifier: Apache-2.0

"""Strict, read-only built-in project tools with policy and audit gating."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from eis.core.audit_chain import AuditIntegrityError
from eis.core.policy_service import PolicyService
from eis.core.project_roots import ProjectRootRecord, ProjectRootRepository
from eis.core.safe_access import (
    SafeAccessError,
    list_directory,
    read_file,
    verify_root,
)
from eis.policy import Action, PolicyContext, PolicyRequest, Principal, Resource
from eis.storage.audit_chain import AuditChainStore

MAX_RELATIVE_PATH_BYTES = 512
MAX_LIST_ENTRIES = 100
MAX_DIRECTORY_SCAN = 5000
MAX_READ_BYTES = 32_768
MAX_TOOL_OUTPUT_BYTES = 65_536
GIT_TIMEOUT_SECONDS = 5

_RESERVED_PARTS = frozenset(
    {".git", ".eis", ".ssh", ".aws", ".gnupg", "keys", "secrets", "credentials"}
)
_RESERVED_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "id_rsa",
        "id_ed25519",
        "authorized_keys",
        "known_hosts",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "token",
        "tokens",
        "api_key",
        "apikey",
        "private_key",
    }
)
_RESERVED_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"})


class ToolRequestError(ValueError):
    """Raised for malformed or unsafe read-only tool requests."""


class ToolExecutionError(RuntimeError):
    """Raised internally with a sanitized, stable error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _EmptyArguments(_StrictModel):
    pass


class _ProjectArguments(_StrictModel):
    project_id: str = Field(min_length=1, max_length=80)


class _ListFilesArguments(_ProjectArguments):
    path: str = Field(default="", max_length=MAX_RELATIVE_PATH_BYTES)
    limit: int = Field(default=100, ge=1, le=MAX_LIST_ENTRIES)


class _ReadTextArguments(_ProjectArguments):
    path: str = Field(min_length=1, max_length=MAX_RELATIVE_PATH_BYTES)
    max_bytes: int = Field(default=MAX_READ_BYTES, ge=1, le=MAX_READ_BYTES)


class _ProjectListCall(_StrictModel):
    tool: Literal["project.list"]
    arguments: _EmptyArguments


class _ListFilesCall(_StrictModel):
    tool: Literal["project.list_files"]
    arguments: _ListFilesArguments


class _ReadTextCall(_StrictModel):
    tool: Literal["project.read_text"]
    arguments: _ReadTextArguments


class _GitStatusCall(_StrictModel):
    tool: Literal["git.status"]
    arguments: _ProjectArguments


_TOOL_ARGUMENT_MODELS: dict[
    str,
    type[_EmptyArguments | _ListFilesArguments | _ReadTextArguments | _ProjectArguments],
] = {
    "project.list": _EmptyArguments,
    "project.list_files": _ListFilesArguments,
    "project.read_text": _ReadTextArguments,
    "git.status": _ProjectArguments,
}


REGISTERED_TOOL_NAMES = frozenset(_TOOL_ARGUMENT_MODELS)


class _ToolCallEnvelope(_StrictModel):
    tool: str
    arguments: dict[str, object]


ToolName = Literal["project.list", "project.list_files", "project.read_text", "git.status"]


@dataclass(frozen=True)
class ToolCall:
    """Validated built-in invocation, constructed only through parse_tool_call."""

    tool: ToolName
    arguments: _EmptyArguments | _ListFilesArguments | _ReadTextArguments | _ProjectArguments

    @classmethod
    def parse(cls, value: object) -> ToolCall:
        return parse_tool_call(value)

    def arguments_dict(self) -> dict[str, object]:
        return self.arguments.model_dump(mode="json")

    @property
    def project_id(self) -> str | None:
        value = getattr(self.arguments, "project_id", None)
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class ToolOutcome:
    invocation_id: str
    tool: str
    status: Literal["success", "denied", "failed"]
    output_sha256: str | None
    output_bytes: int
    policy_decision_id: str
    data: object = None
    error_code: str | None = None


def parse_tool_call(value: object) -> ToolCall:
    """Parse a strict typed call; never infer an action from model text."""
    if isinstance(value, ToolCall):
        value = {"tool": value.tool, "arguments": value.arguments_dict()}
    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, ValueError):
            raise ToolRequestError("invalid_tool_call") from None
    if not isinstance(value, dict) or set(value) != {"tool", "arguments"}:
        raise ToolRequestError("invalid_tool_call")
    try:
        envelope = _ToolCallEnvelope.model_validate(value, strict=True)
    except (ValidationError, TypeError, ValueError):
        raise ToolRequestError("invalid_tool_call") from None
    arguments_model = _TOOL_ARGUMENT_MODELS.get(envelope.tool)
    if arguments_model is None:
        raise ToolRequestError("unknown_tool")
    try:
        parsed_arguments = arguments_model.model_validate(envelope.arguments, strict=True)
    except (ValidationError, TypeError, ValueError):
        raise ToolRequestError("invalid_tool_call") from None
    call = ToolCall(tool=cast(ToolName, envelope.tool), arguments=parsed_arguments)
    if call.project_id is not None:
        try:
            Resource("project", identifier=call.project_id)
        except ValueError:
            raise ToolRequestError("invalid_tool_call") from None
    if isinstance(call.arguments, _ListFilesArguments):
        path = _safe_relative_path(call.arguments.path, allow_empty=True)
        if _is_secret_path(path):
            raise ToolRequestError("secret_path_denied")
        call = ToolCall(
            tool=call.tool,
            arguments=call.arguments.model_copy(update={"path": path}),
        )
    elif isinstance(call.arguments, _ReadTextArguments):
        path = _safe_relative_path(call.arguments.path, allow_empty=False)
        if _is_secret_path(path):
            raise ToolRequestError("secret_path_denied")
        call = ToolCall(
            tool=call.tool,
            arguments=call.arguments.model_copy(update={"path": path}),
        )
    return call


class ReadOnlyProjectTools:
    """Registry containing only bounded, fixed read-only project operations."""

    def __init__(
        self,
        *,
        project_roots: ProjectRootRepository,
        policy: PolicyService,
        audit: AuditChainStore,
    ) -> None:
        self._project_roots = project_roots
        self._policy = policy
        self._audit = audit

    def supports_project(self, project_id: str) -> bool:
        return self._project_roots.get(project_id) is not None

    def invoke(
        self,
        *,
        call: ToolCall,
        task_id: str,
        principal: Principal,
    ) -> ToolOutcome:
        if not isinstance(call, ToolCall):
            raise ToolRequestError("invalid_tool_call")
        call = parse_tool_call({"tool": call.tool, "arguments": call.arguments_dict()})
        if principal.kind not in {"agent", "user"}:
            raise ToolRequestError("invalid_runtime_principal")

        invocation_id = f"tiv_{uuid.uuid4().hex}"
        arguments = call.arguments_dict()
        args_hash = _sha256(_canonical(arguments))
        project_id = call.project_id
        action, resource = _policy_target(call)
        try:
            decision = self._policy.evaluate(
                PolicyRequest(
                    principal=principal,
                    action=Action(action),
                    resource=resource,
                    context=PolicyContext(
                        source="agent" if principal.kind == "agent" else "cli",
                        task_id=task_id,
                    ),
                )
            )
        except AuditIntegrityError:
            raise
        except Exception:
            raise AuditIntegrityError("tool policy audit failed") from None

        if decision.effect != "allow":
            try:
                self._audit.append(
                    event_type="tool_invocation_denied",
                    actor=principal.canonical,
                    subject_type="tool_invocation",
                    subject_id=invocation_id,
                    decision="deny",
                    payload={
                        "invocation_id": invocation_id,
                        "task_id": task_id,
                        "tool": call.tool,
                        "project_id": project_id,
                        "arguments_sha256": args_hash,
                        "policy_decision_id": decision.decision_id,
                        "status": "denied",
                    },
                )
            except AuditIntegrityError:
                raise
            except Exception:
                raise AuditIntegrityError("tool denial audit write failed") from None
            return ToolOutcome(
                invocation_id=invocation_id,
                tool=call.tool,
                status="denied",
                output_sha256=None,
                output_bytes=0,
                policy_decision_id=decision.decision_id,
                error_code="policy_denied",
            )

        try:
            self._audit.append(
                event_type="tool_invocation_started",
                actor=principal.canonical,
                subject_type="tool_invocation",
                subject_id=invocation_id,
                decision="allow",
                payload={
                    "invocation_id": invocation_id,
                    "task_id": task_id,
                    "tool": call.tool,
                    "project_id": project_id,
                    "resource_sha256": _sha256(resource.canonical),
                    "arguments_sha256": args_hash,
                    "policy_decision_id": decision.decision_id,
                },
            )
        except AuditIntegrityError:
            raise
        except Exception:
            raise AuditIntegrityError("tool start audit write failed") from None

        try:
            data = self._execute(call)
            output = _canonical(data).encode("utf-8")
            if len(output) > MAX_TOOL_OUTPUT_BYTES:
                raise ToolExecutionError("output_too_large")
            outcome = ToolOutcome(
                invocation_id=invocation_id,
                tool=call.tool,
                status="success",
                output_sha256=hashlib.sha256(output).hexdigest(),
                output_bytes=len(output),
                policy_decision_id=decision.decision_id,
                data=data,
            )
        except ToolExecutionError as exc:
            outcome = ToolOutcome(
                invocation_id=invocation_id,
                tool=call.tool,
                status="failed",
                output_sha256=None,
                output_bytes=0,
                policy_decision_id=decision.decision_id,
                error_code=exc.code,
            )
        except SafeAccessError as exc:
            outcome = ToolOutcome(
                invocation_id=invocation_id,
                tool=call.tool,
                status="failed",
                output_sha256=None,
                output_bytes=0,
                policy_decision_id=decision.decision_id,
                error_code=exc.code,
            )
        except (OSError, UnicodeError):
            outcome = ToolOutcome(
                invocation_id=invocation_id,
                tool=call.tool,
                status="failed",
                output_sha256=None,
                output_bytes=0,
                policy_decision_id=decision.decision_id,
                error_code="io_error",
            )

        event_type = (
            "tool_invocation_completed" if outcome.status == "success" else "tool_invocation_failed"
        )
        try:
            self._audit.append(
                event_type=event_type,
                actor=principal.canonical,
                subject_type="tool_invocation",
                subject_id=invocation_id,
                decision=outcome.status,
                payload={
                    "invocation_id": invocation_id,
                    "task_id": task_id,
                    "tool": call.tool,
                    "project_id": project_id,
                    "status": outcome.status,
                    "output_sha256": outcome.output_sha256,
                    "output_bytes": outcome.output_bytes,
                    "error_code": outcome.error_code,
                },
            )
        except AuditIntegrityError:
            raise
        except Exception:
            raise AuditIntegrityError("tool outcome audit write failed") from None
        return outcome

    def _execute(self, call: ToolCall) -> object:
        if call.tool == "project.list":
            records = sorted(self._project_roots.list(), key=lambda item: item.project_id)
            return {"projects": [{"project_id": item.project_id} for item in records]}
        project_id = call.project_id
        if project_id is None:
            raise ToolExecutionError("project_unavailable")
        record = self._project_roots.get(project_id)
        if record is None:
            raise ToolExecutionError("project_unavailable")

        if call.tool == "project.list_files":
            list_arguments = cast(_ListFilesArguments, call.arguments)
            relative = _safe_relative_path(list_arguments.path, allow_empty=True)
            entries, truncated = list_directory(
                record,
                relative,
                scan_limit=MAX_DIRECTORY_SCAN,
            )
            selected: list[dict[str, object]] = []
            for entry in entries:
                child_relative = f"{relative}/{entry.name}" if relative else entry.name
                if (
                    _is_secret_path(child_relative)
                    or len(child_relative.encode("utf-8")) > MAX_RELATIVE_PATH_BYTES
                ):
                    continue
                selected.append(
                    {
                        "path": child_relative,
                        "kind": entry.kind,
                        "size": entry.size,
                    }
                )
            selected.sort(key=lambda item: cast(str, item["path"]).casefold())
            if len(selected) > list_arguments.limit:
                selected = selected[: list_arguments.limit]
                truncated = True
            return {"entries": selected, "truncated": truncated}

        if call.tool == "project.read_text":
            text_arguments = cast(_ReadTextArguments, call.arguments)
            relative = _safe_relative_path(text_arguments.path, allow_empty=False)
            content = read_file(record, relative, text_arguments.max_bytes)
            if b"\x00" in content:
                raise ToolExecutionError("not_text")
            return {
                "path": relative,
                "byte_count": len(content),
                "text": content.decode("utf-8"),
            }

        if call.tool == "git.status":
            return self._git_status(record)

        raise ToolExecutionError("unknown_tool")

    def _git_status(self, record: ProjectRootRecord) -> dict[str, object]:
        root = verify_root(record)
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_PAGER": "cat",
            }
        )
        argv = [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "status.submoduleSummary=false",
            "status",
            "--porcelain=v1",
            "--branch",
            "--untracked-files=no",
            "--ignore-submodules=all",
        ]
        try:
            process = subprocess.Popen(
                argv,
                cwd=root,
                env=env,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise ToolExecutionError("git_unavailable") from None

        output = bytearray()
        overflow = threading.Event()

        def drain() -> None:
            stream = process.stdout
            if stream is None:
                return
            while chunk := stream.read(8192):
                remaining = MAX_TOOL_OUTPUT_BYTES - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    process.kill()
                    return

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            process.wait(timeout=GIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            reader.join(timeout=1)
            raise ToolExecutionError("git_timeout") from None
        reader.join(timeout=1)
        if overflow.is_set():
            raise ToolExecutionError("output_too_large")
        if process.returncode != 0:
            raise ToolExecutionError("git_status_failed")

        branch = "unknown"
        counts = {"changed": 0, "added": 0, "deleted": 0, "conflicted": 0}
        for raw_line in output.splitlines():
            line = raw_line.decode("utf-8", errors="replace")
            if line.startswith("## "):
                branch = line[3:256]
                continue
            status = line[:2]
            if "U" in status or status == "AA" or status == "DD":
                counts["conflicted"] += 1
            elif "A" in status or "?" in status:
                counts["added"] += 1
            elif "D" in status:
                counts["deleted"] += 1
            else:
                counts["changed"] += 1
        verify_root(record)
        return {"branch": branch, "counts": counts}


def _policy_target(call: ToolCall) -> tuple[str, Resource]:
    project_id = call.project_id
    if call.tool == "project.list":
        return "project.list", Resource("global")
    if project_id is None:
        raise ToolRequestError("invalid_tool_call")
    Resource("project", identifier=project_id)
    if call.tool == "git.status":
        return "git.read", Resource("project", identifier=project_id)
    if call.tool == "project.list_files":
        list_arguments = cast(_ListFilesArguments, call.arguments)
        path = _safe_relative_path(list_arguments.path, allow_empty=True)
    elif call.tool == "project.read_text":
        text_arguments = cast(_ReadTextArguments, call.arguments)
        path = _safe_relative_path(text_arguments.path, allow_empty=False)
    else:
        raise ToolRequestError("unknown_tool")
    if _is_secret_path(path):
        raise ToolRequestError("secret_path_denied")
    if path == "":
        return "project.read", Resource("project", identifier=project_id)
    return "project.read", Resource.for_path(project_id, path)


def _safe_relative_path(value: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or "\x00" in value or "\\" in value or value.startswith("/"):
        raise ToolRequestError("invalid_relative_path")
    if len(value.encode("utf-8")) > MAX_RELATIVE_PATH_BYTES:
        raise ToolRequestError("invalid_relative_path")
    if value == "":
        if allow_empty:
            return ""
        raise ToolRequestError("invalid_relative_path")
    normalized = unicodedata.normalize("NFC", value)
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} or ":" in part or part != part.strip() for part in parts):
        raise ToolRequestError("invalid_relative_path")
    if len(normalized.encode("utf-8")) > MAX_RELATIVE_PATH_BYTES:
        raise ToolRequestError("invalid_relative_path")
    if os.name == "nt":
        normalized = os.path.normcase(normalized).replace("\\", "/")
    return normalized


def _is_secret_path(relative: str) -> bool:
    for part in relative.split("/"):
        name = part.casefold()
        stem = name.split(".", 1)[0]
        if (
            name in _RESERVED_PARTS
            or name in _RESERVED_NAMES
            or stem in _RESERVED_NAMES
            or name == ".env"
            or name.startswith(".env.")
            or name.endswith(tuple(_RESERVED_SUFFIXES))
        ):
            return True
    return False


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
