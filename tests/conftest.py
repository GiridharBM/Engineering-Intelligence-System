# SPDX-License-Identifier: Apache-2.0

"""Shared test fixtures."""

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_PROVIDER_ENV = (
    "EIS_MODEL_PROVIDER",
    "EIS_MODEL",
    "EIS_MODEL_TIMEOUT_SECONDS",
    "EIS_MODEL_CONSENT_CLASSES",
    "EIS_MODEL_CONSENT_PURPOSE",
    "EIS_OPENCODE_BASE_URL",
    "EIS_OPENCODE_MODEL",
    "EIS_OPENCODE_API_KEY",
    "OPENCODE_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "EIS_DISABLE_DOTENV",
)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session", autouse=True)
def windows_selector_event_loop() -> Iterator[None]:
    """Avoid the Proactor socket pair in restricted Windows sandboxes.

    Python 3.12's default Proactor loop can block while creating its socket
    pair where local socket creation is denied. These tests use mock transports
    and do not need Proactor-only features, so the selector loop is a safe
    test-environment workaround.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    yield


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests deterministic and ensure no real provider key is read."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    yield
