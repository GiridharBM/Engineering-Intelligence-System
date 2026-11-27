# SPDX-License-Identifier: Apache-2.0

"""Bounded process-level smoke tests for the real `eis serve` command.

The real loopback HTTP smoke is gated to the hosted Ubuntu `serve-smoke` job
(``EIS_SERVE_SMOKE=1``). Deterministic tests use controlled subprocesses and a
fake HTTP opener, so they run without binding a socket. Tokens are generated
per test and never printed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from eis import __version__
from eis.core.api_auth import load_or_create_token

LINUX = sys.platform.startswith("linux")
SMOKE_ENABLED = os.environ.get("EIS_SERVE_SMOKE") == "1"


class ServeSmokeError(RuntimeError):
    """Base class for bounded serve-process smoke failures."""


class ServeStartupError(ServeSmokeError):
    """The server process exited before it became ready."""


class ServeReadinessTimeout(ServeSmokeError):
    """The server did not become ready within the bounded timeout."""


class ServeHttpError(ServeSmokeError):
    """A loopback HTTP request failed or returned a non-JSON response."""


@dataclass
class ServeProcess:
    """A bounded child server process plus loopback JSON probes."""

    process: subprocess.Popen[bytes]
    base_url: str
    token: str
    request_timeout: float = 2.0

    @classmethod
    def launch(
        cls,
        *,
        command: Sequence[str],
        env: Mapping[str, str],
        base_url: str,
        token: str,
        cwd: Path | None = None,
        request_timeout: float = 2.0,
    ) -> ServeProcess:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(env),
            cwd=str(cwd) if cwd is not None else None,
        )
        return cls(
            process=process,
            base_url=base_url,
            token=token,
            request_timeout=request_timeout,
        )

    def wait_ready(self, *, timeout: float, poll_interval: float = 0.05) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ServeStartupError(
                    f"server exited before readiness with code {self.process.returncode}"
                )
            try:
                status, _ = self.get_json("/health", authenticated=False)
            except ServeHttpError:
                if self.process.poll() is not None:
                    raise ServeStartupError(
                        f"server exited before readiness with code {self.process.returncode}"
                    ) from None
                time.sleep(poll_interval)
                continue
            if status == 200:
                return
            time.sleep(poll_interval)
        raise ServeReadinessTimeout("server did not become ready within the bounded timeout")

    def get_json(self, path: str, *, authenticated: bool) -> tuple[int, object]:
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.base_url + path, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                status = response.status
                body = response.read(1_000_000)
        except HTTPError as exc:
            status = exc.code
            body = exc.read(1_000_000)
        except (URLError, OSError):
            raise ServeHttpError("loopback request failed") from None
        try:
            return status, json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ServeHttpError("loopback response was not JSON") from None

    def stop(self) -> None:
        process = self.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.poll() is None:
            raise ServeSmokeError("server process did not exit after termination")


def _smoke_env(tmp_path: Path) -> dict[str, str]:
    """Build an environment with temporary state and no provider credentials."""
    env = dict(os.environ)
    for name in list(env):
        if name.startswith(
            ("OPENAI_", "OPENCODE_", "GEMINI_", "GOOGLE_", "ANTHROPIC_", "XAI_", "AWS_")
        ):
            env.pop(name, None)
        if name.startswith("EIS_MODEL") or name.startswith("EIS_OPENCODE"):
            env.pop(name, None)
    env.pop("EIS_POLICY_FILE", None)
    env["EIS_DATA_DIR"] = str(tmp_path / "data")
    env["EIS_CONFIG_DIR"] = str(tmp_path / "config")
    env["EIS_DISABLE_DOTENV"] = "1"
    return env


def _serve_command(*, host: str = "127.0.0.1", port: int) -> list[str]:
    return [sys.executable, "-m", "eis", "serve", "--host", host, "--port", str(port)]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _FakeResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, amount: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_non_loopback_host_is_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        _serve_command(host="0.0.0.0", port=1),
        cwd=tmp_path,
        env=_smoke_env(tmp_path),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 2
    assert "only loopback hosts are allowed" in result.stderr
    assert "0.0.0.0" not in result.stdout


def test_startup_failure_is_bounded_and_reaped(tmp_path: Path) -> None:
    served = ServeProcess.launch(
        command=[sys.executable, "-c", "raise SystemExit(3)"],
        env=_smoke_env(tmp_path),
        base_url="http://127.0.0.1:1",
        token="synthetic-placeholder-token",
        cwd=tmp_path,
        request_timeout=0.1,
    )
    try:
        with pytest.raises(ServeStartupError, match="code 3"):
            served.wait_ready(timeout=2.0, poll_interval=0.05)
    finally:
        served.stop()
    assert served.process.poll() is not None


def test_readiness_timeout_is_bounded_and_reaped(tmp_path: Path) -> None:
    served = ServeProcess.launch(
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
        env=_smoke_env(tmp_path),
        base_url="http://127.0.0.1:1",
        token="synthetic-placeholder-token",
        cwd=tmp_path,
        request_timeout=0.1,
    )
    started = time.monotonic()
    try:
        with pytest.raises(ServeReadinessTimeout):
            served.wait_ready(timeout=0.4, poll_interval=0.05)
    finally:
        served.stop()
    assert time.monotonic() - started < 5.0
    assert served.process.poll() is not None


def test_http_failure_is_reported_without_leaking_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served = ServeProcess.launch(
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
        env=_smoke_env(tmp_path),
        base_url="http://127.0.0.1:1",
        token="synthetic-placeholder-token",
        cwd=tmp_path,
        request_timeout=0.1,
    )

    def fail(*args: object, **kwargs: object) -> object:
        raise URLError("loopback refused")

    monkeypatch.setattr(sys.modules[__name__], "urlopen", fail)
    try:
        with pytest.raises(ServeHttpError) as raised:
            served.get_json("/health", authenticated=True)
    finally:
        served.stop()
    assert "synthetic-placeholder-token" not in str(raised.value)
    assert served.process.poll() is not None


def test_authentication_header_is_only_sent_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served = ServeProcess.launch(
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
        env=_smoke_env(tmp_path),
        base_url="http://127.0.0.1:1",
        token="synthetic-placeholder-token",
        cwd=tmp_path,
    )
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse(b'{"status":"ok"}')

    monkeypatch.setattr(sys.modules[__name__], "urlopen", fake_urlopen)
    try:
        unauth_status, unauth_body = served.get_json("/api/v1/sessions", authenticated=False)
        assert unauth_status == 200
        assert unauth_body == {"status": "ok"}
        assert captured[0].get_header("Authorization") is None

        auth_status, auth_body = served.get_json("/api/v1/sessions", authenticated=True)
        assert auth_status == 200
        assert auth_body == {"status": "ok"}
        assert captured[1].get_header("Authorization") == ("Bearer synthetic-placeholder-token")
    finally:
        served.stop()
    assert served.process.poll() is not None


def test_stop_terminates_and_reaps(tmp_path: Path) -> None:
    served = ServeProcess.launch(
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
        env=_smoke_env(tmp_path),
        base_url="http://127.0.0.1:1",
        token="synthetic-placeholder-token",
        cwd=tmp_path,
    )

    served.stop()
    assert served.process.poll() is not None
    served.stop()
    assert served.process.poll() is not None


@pytest.mark.skipif(
    not (LINUX and SMOKE_ENABLED),
    reason="real loopback serve smoke runs in the hosted Ubuntu serve-smoke job",
)
def test_real_serve_process_health_and_authenticated_route(tmp_path: Path) -> None:
    port = _free_loopback_port()
    token = load_or_create_token(tmp_path / "config")
    served = ServeProcess.launch(
        command=_serve_command(port=port),
        env=_smoke_env(tmp_path),
        base_url=f"http://127.0.0.1:{port}",
        token=token,
        cwd=tmp_path,
        request_timeout=3.0,
    )
    try:
        served.wait_ready(timeout=20.0, poll_interval=0.1)

        health_status, health_body = served.get_json("/health", authenticated=False)
        assert health_status == 200
        assert health_body == {"status": "ok", "name": "EIS", "version": __version__}
        assert token not in json.dumps(health_body)

        unauthenticated_status, _ = served.get_json("/api/v1/sessions", authenticated=False)
        assert unauthenticated_status == 401

        authenticated_status, sessions = served.get_json("/api/v1/sessions", authenticated=True)
        assert authenticated_status == 200
        assert isinstance(sessions, list)
        assert token not in json.dumps(sessions)
        print("serve_smoke=ok health=200 unauth=401 auth=200 cleanup=ok")
    finally:
        served.stop()
    assert served.process.poll() is not None
