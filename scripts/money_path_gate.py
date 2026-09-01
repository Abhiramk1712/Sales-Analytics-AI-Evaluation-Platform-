#!/usr/bin/env python3
"""
scripts/money_path_gate.py
==========================
PreToolUse gate: pause before the first edit to a commission-calculating file
until an approach has been recorded for this branch.

This exists because CLAUDE.md is advisory. A rule written in markdown competes
with everything else for attention and gets missed exactly when the work is
busiest — which is when a money-path mistake is most likely. The gate is
mechanical, so it does not depend on anyone remembering the rule.

It checks verifiable state, not intent: a file must exist at
`.claude/plan-state/<branch>.txt`. Recording an approach is one command, and it
only has to happen once per branch:

    mkdir -p .claude/plan-state
    echo "<approach>" > ".claude/plan-state/$(git rev-parse --abbrev-ref HEAD | tr '/' '_').txt"

Reads the hook payload on stdin, writes a decision as JSON on stdout. Exits 0
in every case — the decision is carried in the JSON, and a crash here must
never block ordinary work, so unexpected failures fall through to "allow".
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

#: Files whose defects change what somebody is paid.
MONEY_PREFIXES = (
    "backend/payout/",
    "backend/metrics/",
)
MONEY_FILES = (
    "backend/audit/payout_audit.py",
)

#: Tests are deliberately exempt. Writing the test first is the behaviour this
#: gate is trying to produce; gating it would punish the right move.
TEST_MARKERS = ("tests/", "test_")


def allow() -> None:
    print(json.dumps({}))
    sys.exit(0)


def ask(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def repo_relative(path_str: str, root: Path) -> str | None:
    try:
        return str(Path(path_str).resolve().relative_to(root)).replace("\\", "/")
    except (ValueError, OSError):
        return None


def current_branch(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        allow()

    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not isinstance(file_path, str) or not file_path:
        allow()

    root = Path(__file__).resolve().parent.parent
    rel = repo_relative(file_path, root)
    if rel is None:
        allow()  # outside this repo — not ours to gate

    if any(marker in rel for marker in TEST_MARKERS):
        allow()

    is_money = rel.startswith(MONEY_PREFIXES) or rel in MONEY_FILES
    if not is_money:
        allow()

    branch = current_branch(root)
    if branch is None:
        allow()  # not a git checkout — nothing to key the record to

    state = root / ".claude" / "plan-state" / f"{branch.replace('/', '_')}.txt"
    if state.is_file() and state.read_text(encoding="utf-8").strip():
        allow()

    ask(
        f"{rel} calculates what someone is paid, and no approach is recorded for "
        f"branch '{branch}'.\n\n"
        "State the intended change in a sentence or two first, and say at what "
        "GRAIN the calculation applies — per credit, per rep, per period. Getting "
        "that wrong is how tiers were evaluated per sales credit instead of "
        "cumulatively, misallocating $1.2M across 152 of 189 rep-quarters in the "
        "reference data.\n\n"
        "Once agreed, record it and this stops asking for the rest of the branch:\n"
        "  mkdir -p .claude/plan-state\n"
        f"  echo \"<approach>\" > \"{state.relative_to(root)}\"\n\n"
        "Approving here bypasses the gate, which is correct for a typo or a comment."
    )


if __name__ == "__main__":
    main()
