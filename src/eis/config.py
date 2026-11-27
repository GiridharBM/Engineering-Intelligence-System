# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration, provider selection and local consent settings.

No provider is enabled by default. Credentials are read only from the process
environment (optionally loaded from a local ignored ``.env``) and are masked in
representations.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from pydantic import SecretStr

SESSION_RETENTION_DAYS = 7
AUDIT_RETENTION_DAYS = 365
ENGINEERING_RETENTION = "until_deleted"
UNTRUSTED_EXECUTION_POLICY = "require_t2_or_refuse"

ModelProviderName = Literal["opencode", "openai", "gemini", "anthropic", "xai", "ollama"]
DataClass = Literal["C0", "C1", "C2", "C3"]

DEFAULT_MODEL_PROVIDER: ModelProviderName | None = None
EXTERNAL_PROVIDERS = frozenset({"opencode", "openai", "gemini", "anthropic", "xai"})
_EXTERNAL_PROVIDERS = EXTERNAL_PROVIDERS
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_CONSENT_CLASSES = frozenset({"C0", "C1", "C2"})

_OFFICIAL_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic": "https://api.anthropic.com/v1",
    "xai": "https://api.x.ai/v1",
}
_KEY_ENV = {
    "opencode": ("OPENCODE_API_KEY", "EIS_OPENCODE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "xai": ("XAI_API_KEY",),
}


class ConfigurationError(ValueError):
    """Raised for missing or malformed configuration; never includes secrets."""


@dataclass(frozen=True)
class ProviderConfig:
    """A validated provider connection. The key is masked in reprs."""

    provider: ModelProviderName
    model: str
    api_key: SecretStr
    base_url: str
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class ConsentRecord:
    """Explicit, provider-scoped consent for data classes in this process."""

    consent_id: str
    provider: ModelProviderName
    data_classes: frozenset[DataClass]
    purpose: str
    expires_at_ms: int | None = None
    revoked_at_ms: int | None = None


@dataclass(frozen=True)
class Settings:
    """Process settings with privacy-safe defaults."""

    data_dir: Path
    config_dir: Path
    project_roots: tuple[Path, ...] = ()
    model_provider: ModelProviderName | None = DEFAULT_MODEL_PROVIDER
    provider_config: ProviderConfig | None = None
    consent: ConsentRecord | None = None
    allow_cloud: bool = False
    session_retention_days: int = SESSION_RETENTION_DAYS
    audit_retention_days: int = AUDIT_RETENTION_DAYS
    engineering_retention: str = ENGINEERING_RETENTION

    @property
    def model_configured(self) -> bool:
        return self.provider_config is not None

    @property
    def opencode(self) -> ProviderConfig | None:
        """Backward-compatible view used by earlier provider tests/API callers."""
        config = self.provider_config
        if config is not None and config.provider == "opencode":
            return config
        return None


def _platform_data_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "EIS" if base else Path.home() / "AppData" / "Local" / "EIS"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "EIS"
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / "eis" if base else Path.home() / ".local" / "share" / "eis"


def _platform_config_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA")
        return Path(base) / "EIS" if base else Path.home() / "AppData" / "Roaming" / "EIS"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "EIS"
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / "eis" if base else Path.home() / ".config" / "eis"


def _resolved(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _required(environ: Mapping[str, str], name: str, purpose: str) -> str:
    value = environ.get(name)
    if value is None or not value.strip():
        raise ConfigurationError(f"{name} is required {purpose}")
    return value.strip()


def _validate_base_url(value: str) -> str:
    if any(character.isspace() for character in value):
        raise ConfigurationError("EIS_OPENCODE_BASE_URL must not contain whitespace")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("EIS_OPENCODE_BASE_URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ConfigurationError("EIS_OPENCODE_BASE_URL must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ConfigurationError("EIS_OPENCODE_BASE_URL must not contain a query or fragment")
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise ConfigurationError("EIS_OPENCODE_BASE_URL must use HTTPS unless the host is loopback")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validate_model(value: str) -> str:
    if len(value) > 200 or not _MODEL_RE.fullmatch(value):
        raise ConfigurationError(
            "EIS_MODEL must be a direct model identifier without spaces or wildcards"
        )
    return value


def _validate_api_key(value: str, source: str) -> SecretStr:
    if any(character.isspace() for character in value):
        raise ConfigurationError(f"{source} must not contain whitespace")
    if len(value) > 4096:
        raise ConfigurationError(f"{source} is too long")
    return SecretStr(value)


def _timeout_seconds(environ: Mapping[str, str], provider: ModelProviderName) -> float:
    raw = environ.get("EIS_MODEL_TIMEOUT_SECONDS")
    if raw is None and provider == "opencode":
        raw = environ.get("EIS_OPENCODE_TIMEOUT_SECONDS")
    if raw is None or not raw.strip():
        return 60.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError("EIS_MODEL_TIMEOUT_SECONDS must be a number") from exc
    if not 1.0 <= value <= 300.0:
        raise ConfigurationError("EIS_MODEL_TIMEOUT_SECONDS must be between 1 and 300")
    return value


def _provider_key(environ: Mapping[str, str], provider: ModelProviderName) -> SecretStr:
    names = _KEY_ENV.get(provider)
    if names is None:
        raise ConfigurationError(f"provider {provider} has no API-key configuration")
    for name in names:
        raw = environ.get(name)
        if raw is not None and raw.strip():
            return _validate_api_key(raw.strip(), name)
    options = " or ".join(names)
    raise ConfigurationError(f"{options} is required when EIS_MODEL_PROVIDER={provider}")


def load_provider_config(
    provider: ModelProviderName, environ: Mapping[str, str] | None = None
) -> ProviderConfig:
    """Validate configuration for one external provider without network access."""
    if provider not in _EXTERNAL_PROVIDERS:
        raise ConfigurationError(f"provider {provider} is not an external provider")
    env = os.environ if environ is None else environ
    model_name = "EIS_OPENCODE_MODEL" if provider == "opencode" else "EIS_MODEL"
    model = _validate_model(
        _required(env, "EIS_MODEL", "when a provider is selected")
        if env.get("EIS_MODEL", "").strip()
        else _required(env, model_name, "when a provider is selected")
    )
    if provider == "opencode":
        base_url = _validate_base_url(
            _required(env, "EIS_OPENCODE_BASE_URL", "when EIS_MODEL_PROVIDER=opencode")
        )
    else:
        base_url = _OFFICIAL_BASE_URLS[provider]
    return ProviderConfig(
        provider=provider,
        model=model,
        api_key=_provider_key(env, provider),
        base_url=base_url,
        timeout_seconds=_timeout_seconds(env, provider),
    )


def load_consent(
    provider: ModelProviderName, environ: Mapping[str, str] | None = None
) -> ConsentRecord | None:
    """Load explicit consent for the selected provider.

    ``EIS_MODEL_CONSENT_CLASSES`` is a comma-separated subset of C0, C1 and C2.
    C3 is never allowed. An absent or empty value means no network consent.
    """
    if provider not in _EXTERNAL_PROVIDERS:
        return None
    env = os.environ if environ is None else environ
    raw = env.get("EIS_MODEL_CONSENT_CLASSES", "")
    values = {item.strip().upper() for item in raw.split(",") if item.strip()}
    if not values:
        return None
    if "C3" in values:
        raise ConfigurationError("C3 data can never be sent to an external model provider")
    unknown = values - _CONSENT_CLASSES
    if unknown:
        raise ConfigurationError("EIS_MODEL_CONSENT_CLASSES must contain only C0, C1 or C2")
    purpose = env.get("EIS_MODEL_CONSENT_PURPOSE", "model completion").strip()
    if not purpose:
        purpose = "model completion"
    classes = frozenset(cast(DataClass, value) for value in values)
    digest = hashlib.sha256(
        f"{provider}:{','.join(sorted(classes))}:{purpose}".encode()
    ).hexdigest()
    return ConsentRecord(
        consent_id=f"cns_{digest[:16]}",
        provider=provider,
        data_classes=classes,
        purpose=purpose,
    )


def load_settings(
    environ: Mapping[str, str] | None = None,
    *,
    require_provider: bool = True,
) -> Settings:
    """Load settings from environment overrides and platform defaults only."""
    env = os.environ if environ is None else environ
    data = env.get("EIS_DATA_DIR")
    config = env.get("EIS_CONFIG_DIR")

    raw_provider = env.get("EIS_MODEL_PROVIDER", "").strip().lower()
    provider: ModelProviderName | None
    provider_config: ProviderConfig | None
    consent: ConsentRecord | None
    if not raw_provider:
        provider = None
        provider_config = None
        consent = None
    elif raw_provider == "ollama":
        provider = "ollama"
        provider_config = None
        consent = None
    elif raw_provider in _EXTERNAL_PROVIDERS:
        provider = cast(ModelProviderName, raw_provider)
        if require_provider:
            provider_config = load_provider_config(provider, env)
            consent = load_consent(provider, env)
        else:
            provider_config = None
            consent = None
    else:
        raise ConfigurationError(
            "EIS_MODEL_PROVIDER must be opencode, openai, gemini, anthropic, xai, ollama, or unset"
        )

    return Settings(
        data_dir=_resolved(data) if data else _platform_data_dir().resolve(),
        config_dir=_resolved(config) if config else _platform_config_dir().resolve(),
        model_provider=provider,
        provider_config=provider_config,
        consent=consent,
        allow_cloud=provider in _EXTERNAL_PROVIDERS,
    )


def load_local_env(path: Path | None = None) -> bool:
    """Load a local ``.env`` into the process environment without printing it."""
    if os.environ.get("EIS_DISABLE_DOTENV") == "1":
        return False
    dotenv_path = path if path is not None else Path.cwd() / ".env"
    return bool(load_dotenv(dotenv_path=dotenv_path, override=False))


def is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is ``root`` or a descendant of it."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
