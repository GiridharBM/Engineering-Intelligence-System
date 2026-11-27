# SPDX-License-Identifier: Apache-2.0

"""CLI contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from eis import __version__
from eis.cli import app

runner = CliRunner()
FAKE_KEY = "test-only-secret-value"
OFFICIAL_BASE = "https://opencode.ai/inference/openai/v1"


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"eis {__version__}" in result.output


def test_doctor_reports_safe_defaults() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "project_roots=0" in result.output
    assert "model_provider=none" in result.output
    assert "model_configured=false" in result.output
    assert "allow_cloud=false" in result.output
    assert "policy_default=deny" in result.output
    assert "session_retention_days=7" in result.output
    assert "audit_retention_days=365" in result.output
    assert "engineering_retention=until_deleted" in result.output
    assert "untrusted_execution=require_t2_or_refuse" in result.output


def test_provider_check_reports_unconfigured_default() -> None:
    result = runner.invoke(app, ["provider-check"])
    assert result.exit_code == 0
    assert "provider=none configured=false" in result.output


def test_provider_check_reports_configuration_without_the_key(monkeypatch) -> None:
    monkeypatch.setenv("EIS_MODEL_PROVIDER", "opencode")
    monkeypatch.setenv("EIS_OPENCODE_BASE_URL", OFFICIAL_BASE)
    monkeypatch.setenv("EIS_OPENCODE_MODEL", "test-model")
    monkeypatch.setenv("OPENCODE_API_KEY", FAKE_KEY)

    result = runner.invoke(app, ["provider-check"])
    assert result.exit_code == 0, result.output
    assert "provider=opencode configured=true" in result.output
    assert "model=test-model" in result.output
    assert f"base_url={OFFICIAL_BASE}" in result.output
    assert "api_key=set" in result.output
    assert FAKE_KEY not in result.output


def test_ask_fake_runs_durable_plan_without_a_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path / "config"))
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": "m1-ask-fake-v1",
                "default": "deny",
                "rules": [
                    {
                        "id": "run",
                        "principal": "agent:eis.primary",
                        "action": "task.run",
                        "resource": "global",
                        "effect": "allow",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["ask", "private-goal-marker", "--fake", "--policy-file", str(policy_path)]
    )

    assert result.exit_code == 0, result.output
    assert "state=blocked" in result.output
    assert "stop_reason=human_review_required" in result.output
    assert "private-goal-marker" not in result.output


def test_doctor_reports_safe_configuration_error(monkeypatch) -> None:
    monkeypatch.setenv("EIS_MODEL_PROVIDER", "opencode")
    monkeypatch.setenv("EIS_OPENCODE_BASE_URL", "not-a-url")
    monkeypatch.setenv("EIS_OPENCODE_MODEL", "test-model")
    monkeypatch.setenv("OPENCODE_API_KEY", FAKE_KEY)

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "configuration_error" in result.output
    assert "EIS_OPENCODE_BASE_URL" in result.output
    assert FAKE_KEY not in result.output


def test_policy_check_denies_unspecified_action() -> None:
    result = runner.invoke(app, ["policy-check"])
    assert result.exit_code == 0
    assert "probe=deny" in result.output
    assert "reason=default_deny" in result.output
    assert "policy_version=2026.11-m1a" in result.output


def test_privacy_check_is_clean() -> None:
    result = runner.invoke(app, ["privacy-check"])
    assert result.exit_code == 0, result.output
    assert "privacy: ok" in result.output
