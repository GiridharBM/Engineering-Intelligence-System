"""Import and packaging contract tests."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import typer
from fastapi import FastAPI

from eis import __version__
from eis.api import app as api_app
from eis.api import create_app
from eis.cli import app as cli_app

ROOT = Path(__file__).resolve().parents[1]


def test_api_import_contract() -> None:
    assert callable(create_app)
    assert isinstance(api_app, FastAPI)


def test_cli_import_contract() -> None:
    assert isinstance(cli_app, typer.Typer)


def test_package_version_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__
    assert project["project"]["requires-python"] == ">=3.11,<3.13"


def test_module_entrypoint() -> None:
    env = {**os.environ, "EIS_DISABLE_DOTENV": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "eis", "version"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert f"eis {__version__}" in result.stdout
