# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration, provider selection and consent tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eis.config import (
    AUDIT_RETENTION_DAYS,
    DEFAULT_MODEL_PROVIDER,
    ENGINEERING_RETENTION,
    SESSION_RETENTION_DAYS,
    UNTRUSTED_EXECUTION_POLICY,
    ConfigurationError,
    is_within,
    load_consent,
    load_local_env,
    load_provider_config,
    load_settings,
)

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "test-only-secret-value"
OFFICIAL_BASES = {
    "opencode": "https://opencode.ai/inference/openai/v1",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic": "https://api.anthropic.com/v1",
    "xai": "https://api.x.ai/v1",
}
KEY_NAMES = {
    "opencode": "OPENCODE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
}


def _provider_env(provider: str, **overrides: str) -> dict[str, str]:
    env = {
        "EIS_MODEL_PROVIDER": provider,
        "EIS_MODEL": "test-model",
        KEY_NAMES[provider]: FAKE_KEY,
    }
    if provider == "opencode":
        env["EIS_OPENCODE_BASE_URL"] = OFFICIAL_BASES[provider]
    env.update(overrides)
    return env


def test_defaults_have_no_provider_and_no_consent() -> None:
    settings = load_settings(environ={})
    assert settings.project_roots == ()
    assert settings.model_provider is DEFAULT_MODEL_PROVIDER is None
    assert settings.model_configured is False
    assert settings.consent is None
    assert settings.allow_cloud is False
    assert settings.session_retention_days == SESSION_RETENTION_DAYS == 7
    assert settings.audit_retention_days == AUDIT_RETENTION_DAYS == 365
    assert settings.engineering_retention == ENGINEERING_RETENTION == "until_deleted"
    assert UNTRUSTED_EXECUTION_POLICY == "require_t2_or_refuse"


def test_runtime_data_is_outside_the_repository() -> None:
    assert not is_within(load_settings(environ={}).data_dir, ROOT)


def test_environment_override_is_resolved(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    settings = load_settings(
        environ={"EIS_DATA_DIR": str(data_dir), "EIS_CONFIG_DIR": str(config_dir)}
    )
    assert settings.data_dir == data_dir.resolve()
    assert settings.config_dir == config_dir.resolve()


@pytest.mark.parametrize("provider", ["opencode", "openai", "gemini", "anthropic", "xai"])
def test_each_external_provider_has_validated_settings(provider: str) -> None:
    settings = load_settings(environ=_provider_env(provider))
    assert settings.model_provider == provider
    assert settings.model_configured is True
    assert settings.allow_cloud is True
    assert settings.provider_config is not None
    assert settings.provider_config.provider == provider
    assert settings.provider_config.model == "test-model"
    assert settings.provider_config.base_url == OFFICIAL_BASES[provider]
    assert settings.provider_config.api_key.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in repr(settings)
    assert "SecretStr" in repr(settings)


@pytest.mark.parametrize("provider", ["opencode", "openai", "gemini", "anthropic", "xai"])
def test_missing_keys_name_the_variable_only(provider: str) -> None:
    env = _provider_env(provider)
    del env[KEY_NAMES[provider]]
    with pytest.raises(ConfigurationError) as exc:
        load_provider_config(provider, env)  # type: ignore[arg-type]
    assert KEY_NAMES[provider] in str(exc.value)
    assert FAKE_KEY not in str(exc.value)


def test_provider_scoped_consent_is_typed_and_never_includes_c3() -> None:
    env = _provider_env("openai", EIS_MODEL_CONSENT_CLASSES="C0,C1")
    settings = load_settings(environ=env)
    assert settings.consent is not None
    assert settings.consent.provider == "openai"
    assert settings.consent.data_classes == {"C0", "C1"}
    assert settings.consent.consent_id.startswith("cns_")
    assert settings.consent.consent_id not in repr(settings.provider_config)

    with pytest.raises(ConfigurationError):
        load_consent("openai", {"EIS_MODEL_CONSENT_CLASSES": "C3"})
    with pytest.raises(ConfigurationError):
        load_consent("openai", {"EIS_MODEL_CONSENT_CLASSES": "C4"})


def test_no_consent_by_default() -> None:
    assert load_consent("openai", {}) is None
    assert load_settings(environ=_provider_env("openai")).consent is None


def test_opencode_base_url_is_required_and_validated() -> None:
    env = _provider_env("opencode")
    del env["EIS_OPENCODE_BASE_URL"]
    with pytest.raises(ConfigurationError) as exc:
        load_provider_config("opencode", env)
    assert "EIS_OPENCODE_BASE_URL" in str(exc.value)

    for base_url in (
        "not-a-url",
        "http://example.com/v1",
        "https://user:password@example.com/v1",
        "https://example.com/v1?token=value",
    ):
        with pytest.raises(ConfigurationError):
            load_provider_config(
                "opencode",
                _provider_env("opencode", EIS_OPENCODE_BASE_URL=base_url),
            )


def test_loopback_http_is_allowed_for_opencode() -> None:
    config = load_provider_config(
        "opencode",
        _provider_env("opencode", EIS_OPENCODE_BASE_URL="http://127.0.0.1:8080/v1"),
    )
    assert config.base_url == "http://127.0.0.1:8080/v1"


def test_model_and_key_validation_does_not_echo_values() -> None:
    with pytest.raises(ConfigurationError) as exc:
        load_provider_config("openai", _provider_env("openai", EIS_MODEL="bad model"))
    assert "EIS_MODEL" in str(exc.value)
    assert "bad model" not in str(exc.value)

    with pytest.raises(ConfigurationError) as exc:
        load_provider_config("openai", _provider_env("openai", OPENAI_API_KEY="secret with space"))
    assert "OPENAI_API_KEY" in str(exc.value)
    assert "secret with space" not in str(exc.value)


def test_timeout_and_unknown_provider_validation() -> None:
    with pytest.raises(ConfigurationError):
        load_provider_config("openai", _provider_env("openai", EIS_MODEL_TIMEOUT_SECONDS="0"))
    with pytest.raises(ConfigurationError):
        load_settings(environ={"EIS_MODEL_PROVIDER": "not-a-provider"})


def test_ollama_is_optional_and_not_required() -> None:
    settings = load_settings(environ={"EIS_MODEL_PROVIDER": "ollama"})
    assert settings.model_provider == "ollama"
    assert settings.provider_config is None
    assert settings.model_configured is False
    assert settings.allow_cloud is False

    openai = load_settings(environ=_provider_env("openai"))
    assert openai.provider_config is not None
    assert openai.provider_config.provider == "openai"


def test_local_env_loading_is_explicit_and_can_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EIS_MODEL_PROVIDER=openai\n", encoding="utf-8")
    monkeypatch.delenv("EIS_MODEL_PROVIDER", raising=False)
    assert load_local_env(env_file) is True
    assert os.environ.get("EIS_MODEL_PROVIDER") == "openai"
    monkeypatch.delenv("EIS_MODEL_PROVIDER", raising=False)

    monkeypatch.setenv("EIS_DISABLE_DOTENV", "1")
    assert load_local_env(env_file) is False


def test_env_template_contains_only_placeholders() -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "EIS_MODEL_PROVIDER=" in template
    assert "OPENAI_API_KEY=replace-with" in template
    assert "GEMINI_API_KEY=replace-with" in template
    assert "ANTHROPIC_API_KEY=replace-with" in template
    assert "XAI_API_KEY=replace-with" in template
    assert "OPENCODE_API_KEY=replace-with" in template
    assert FAKE_KEY not in template
    assert "sk-" not in template


def test_env_file_is_ignored_in_gitignore() -> None:
    ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore_text
    assert "!.env.example" in ignore_text
