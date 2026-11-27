"""CI supply-chain policy: every GitHub Action reference is SHA-pinned."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_USES = re.compile(r"^\s*-\s*uses:\s*([^\s#]+)")


def test_all_workflow_actions_use_full_commit_shas() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflow files found"
    for workflow in workflows:
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = _USES.match(line)
            if match is None:
                continue
            reference = match.group(1)
            assert "@" in reference, f"{workflow.name}:{number} lacks a pin"
            _, revision = reference.rsplit("@", 1)
            assert re.fullmatch(r"[0-9a-f]{40}", revision), (
                f"{workflow.name}:{number} is not pinned to a full commit SHA"
            )
