# SPDX-License-Identifier: Apache-2.0

"""Guardrails for `.github/workflows/ci.yml`.

The workflow is the enforcement point for M0.2 acceptance, so its safety
properties are asserted by a test rather than by review memory: least-privilege
permissions, full-SHA action pins, deterministic locked dependency sync, the
required check set, and no reliance on secrets, `.env` or live providers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"

USES_RE = re.compile(r"(?m)^\s*(?:-\s+)?uses:\s*([^\s#]+)")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SECRETS_RE = re.compile(r"secrets\.([A-Za-z0-9_]+)")
JOBS = ("lint", "typecheck", "test", "security", "privacy", "package")


def _text() -> str:
    if not CI.exists():
        pytest.skip("workflow not packaged in this tree")
    return CI.read_text(encoding="utf-8")


def test_permissions_are_read_only() -> None:
    text = _text()
    assert re.search(r"(?m)^permissions:\n  contents: read$", text)
    assert "write-all" not in text
    for match in re.finditer(r"(?m)^\s+([a-z-]+):\s*(read|write)$", text):
        assert match.group(1) == "contents" and match.group(2) == "read"


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    text = _text()
    uses = USES_RE.findall(text)
    assert uses, "workflow has no actions"
    for ref in uses:
        if ref.startswith("./"):
            continue
        action, _, sha = ref.partition("@")
        assert "/" in action, ref
        assert SHA_RE.fullmatch(sha), f"action not pinned to a full SHA: {ref}"
    # Version comments keep the pin reviewable (repository policy).
    for line in text.splitlines():
        if "uses:" in line and not line.lstrip().startswith("#"):
            assert re.search(r"# v\d+", line), f"pin without a version comment: {line.strip()}"


def test_no_untrusted_triggers_and_no_repository_secrets() -> None:
    text = _text()
    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    assert set(SECRETS_RE.findall(text)) <= {"GITHUB_TOKEN"}


def test_required_checks_are_present() -> None:
    text = _text()
    for job in JOBS:
        assert re.search(rf"(?m)^  {job}:", text), f"missing job: {job}"
    for command in (
        "uv lock --check",
        "uv sync --locked",
        "ruff check .",
        "ruff format --check .",
        "mypy src",
        "pytest -q",
        "pip-audit",
        "eis privacy-check",
        "uv build",
        "scripts/verify_wheel.py",
        "gitleaks/gitleaks-action@",
    ):
        assert command in text, f"missing check: {command}"
    assert "os: [ubuntu-latest, windows-latest, macos-latest]" in text
    assert 'python: ["3.11", "3.12"]' in text


def test_dependency_sync_is_deterministic() -> None:
    text = _text()
    for line in text.splitlines():
        if "uv sync" in line:
            assert "--locked" in line, f"uv sync without --locked: {line.strip()}"
        if "uv run" in line and "--no-project" not in line:
            assert "--locked" in line, f"uv run without --locked: {line.strip()}"


def test_jobs_are_bounded_and_need_no_local_state() -> None:
    text = _text()
    runs = len(re.findall(r"(?m)^\s+runs-on:", text))
    timeouts = len(re.findall(r"(?m)^\s+timeout-minutes:", text))
    assert runs == timeouts and runs >= len(JOBS)
    # No local secrets, provider configuration or environment overrides.
    assert ".env" not in text
    assert "API_KEY" not in text
    assert re.search(r"EIS_[A-Z_]+", text) is None
    assert "provider" not in text.lower()
