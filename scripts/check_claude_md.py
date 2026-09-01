#!/usr/bin/env python3
"""
scripts/check_claude_md.py
==========================
Hold CLAUDE.md's checkable claims against the repository.

CLAUDE.md is read at the start of every session and treated as instruction, so a
stale line in it is worse than a stale line in the README — it actively misleads.
This repo has already produced three of those: the README documented
`alembic upgrade head` when every alembic command crashed on a duplicate config
key, it put the frontend on port 5173 when vite.config.js says 3000, and
database/schema.sql drifted two tables behind the ORM.

Only claims that can drift are checked. Prose and rationale are not checkable and
are left alone.

    python3 scripts/check_claude_md.py            # report, exit 0 unless drifted
    python3 scripts/check_claude_md.py --quiet    # only print when something drifted
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"


def reexec_in_venv() -> None:
    """
    Re-run under the project virtualenv when we are not already in it.

    Hooks invoke this with whatever `python3` is on PATH, which is the system
    interpreter — where sqlalchemy and alembic are absent. Without this, every
    session start reported a false DRIFT for tools that are installed perfectly
    well, and a checker that cries wolf gets ignored, which defeats its purpose.
    """
    venv_python = ROOT / ".venv" / "bin" / "python"
    if not venv_python.is_file():
        return
    if Path(sys.executable).resolve() == venv_python.resolve():
        return
    import os

    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def claims_text() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


# ── Individual checks: each returns (ok, message) ────────────────────────────

def check_make_targets(text: str) -> tuple[bool, str]:
    """Every `make X` named in CLAUDE.md must be a real target."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    real = set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, re.MULTILINE))
    # Search only code contexts — fenced blocks and inline backticks. Matching
    # `make x` anywhere in the prose also picks up ordinary English ("make
    # targets, the frontend port..."), and a line-start rule does not help
    # because prose wraps. This check flagged its own documentation twice
    # before being scoped this way.
    code = "\n".join(re.findall(r"```[a-z]*\n(.*?)```", text, re.DOTALL))
    code += "\n" + "\n".join(re.findall(r"`([^`\n]+)`", text))
    claimed = set(re.findall(r"\bmake ([a-z][\w-]*)", code))
    missing = sorted(claimed - real)
    if missing:
        return False, f"CLAUDE.md names make targets that do not exist: {missing}"
    return True, f"make targets: {len(claimed)} named, all present"


def check_test_count(text: str) -> tuple[bool, str]:
    """The test-count claim must be within a sane margin of reality."""
    m = re.search(r"(\d[\d,]*)\s+tests", text)
    if not m:
        return True, "test count: not claimed, nothing to check"
    claimed = int(m.group(1).replace(",", ""))

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    found = re.search(r"(\d+) tests? collected", proc.stdout)
    if not found:
        return True, "test count: could not collect, skipping (not a drift signal)"
    actual = int(found.group(1))

    # A few tests added since the file was written is normal; a large gap means
    # the number was copied forward without being re-measured.
    if abs(actual - claimed) > max(25, claimed * 0.05):
        return False, f"CLAUDE.md claims {claimed} tests; pytest collects {actual}"
    return True, f"test count: claims {claimed}, collects {actual}"


def check_tenant_tables(text: str) -> tuple[bool, str]:
    """The "N of M tables" tenancy claim must match the models."""
    m = re.search(r"company_id.{0,40}?(\d+) of (\d+) tables", text, re.DOTALL)
    if not m:
        return True, "tenant tables: not claimed, nothing to check"
    claimed_scoped, claimed_total = int(m.group(1)), int(m.group(2))

    sys.path.insert(0, str(ROOT))
    try:
        import backend.models as models
    except Exception as exc:  # pragma: no cover - import environment
        return True, f"tenant tables: could not import models ({exc}), skipping"

    tables = models.Base.metadata.tables
    total = len(tables)
    scoped = sum(1 for t in tables.values() if "company_id" in t.c)
    if (scoped, total) != (claimed_scoped, claimed_total):
        return False, (
            f"CLAUDE.md claims company_id on {claimed_scoped} of {claimed_total} "
            f"tables; models have {scoped} of {total}"
        )
    return True, f"tenant tables: {scoped} of {total}, as claimed"


def check_frontend_port(text: str) -> tuple[bool, str]:
    """
    The documented dev-server port must match vite.config.js.

    Scoped per line rather than to the whole file: the right port appears in
    several places, so a document-wide substring search passes even while a
    specific line names the wrong one — which is exactly how the README came to
    say 5173 while vite.config.js said 3000.
    """
    vite = (ROOT / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    m = re.search(r"port:\s*(\d+)", vite)
    if not m:
        return True, "frontend port: not set in vite.config.js, skipping"
    actual = m.group(1)

    # A frontend line may legitimately name other ports — the proxy target, for
    # one. What is not legitimate is a frontend line that names ports and never
    # names the real one.
    bad_lines: list[str] = []
    for line in text.splitlines():
        if not re.search(r"\b(vite|frontend)\b", line, re.IGNORECASE):
            continue
        ports = re.findall(r":(\d{4,5})\b", line)
        if ports and actual not in ports:
            bad_lines.append(f"{sorted(set(ports))} in {line.strip()[:60]!r}")

    if bad_lines:
        return False, (
            f"vite.config.js serves on {actual}, but CLAUDE.md gives a frontend "
            f"address without it: {bad_lines[0]}"
        )
    if actual not in text:
        return False, f"CLAUDE.md never mentions the real vite port {actual}"
    return True, f"frontend port: {actual}, as documented"


def check_alembic_runs() -> tuple[bool, str]:
    """Alembic must actually be invocable — it silently was not for a long time."""
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        combined = (proc.stderr or proc.stdout).strip()
        # Alembic not installed is an environment gap, not a documentation drift.
        if "No module named" in combined:
            return True, "alembic: not installed in this interpreter, skipping"
        tail = combined.splitlines()[-1:] or ["(no output)"]
        return False, f"`alembic heads` fails: {tail[0]}"
    return True, f"alembic: runs, head is {proc.stdout.split()[0] if proc.stdout.split() else '?'}"


def check_ci_gates(text: str) -> tuple[bool, str]:
    """
    The "what is enforced" table must match what CI actually runs. A file that
    overstates enforcement is the failure mode the table exists to prevent.
    """
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    expected = {
        "pytest": "pytest" in ci,
        "check_package_hygiene": "check_package_hygiene" in ci,
        "npm run build": "npm run build" in ci,
    }
    missing = [name for name, present in expected.items() if not present]
    if missing:
        return False, f"CLAUDE.md says CI enforces {missing}, but ci.yml does not run it"

    # And the honest gaps must still be gaps.
    for tool, label in (("eslint", "frontend lint"), ("--cov", "backend coverage")):
        if tool in ci.lower():
            return False, (
                f"ci.yml now runs {tool}; CLAUDE.md still lists {label} as not enforced"
            )
    return True, "CI gates: pytest, hygiene and build enforced; stated gaps still gaps"


CHECKS = (
    ("make targets", check_make_targets, True),
    ("frontend port", check_frontend_port, True),
    ("tenant tables", check_tenant_tables, True),
    ("CI gates", check_ci_gates, True),
    ("alembic", check_alembic_runs, False),
    ("test count", check_test_count, True),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="only print on drift")
    parser.add_argument("--skip-slow", action="store_true", help="skip test collection")
    args = parser.parse_args()

    reexec_in_venv()

    if not CLAUDE_MD.is_file():
        print("CLAUDE.md not found — nothing to check.")
        return 0

    text = claims_text()
    results: list[tuple[str, bool, str]] = []

    for name, fn, needs_text in CHECKS:
        if args.skip_slow and name == "test count":
            continue
        try:
            ok, message = fn(text) if needs_text else fn()
        except Exception as exc:  # a broken check must not block work
            ok, message = True, f"{name}: check errored ({exc}), skipping"
        results.append((name, ok, message))

    drifted = [r for r in results if not r[1]]

    if drifted or not args.quiet:
        print("CLAUDE.md drift check")
        for name, ok, message in results:
            print(f"  [{'ok  ' if ok else 'DRIFT'}] {message}")

    if drifted:
        print(f"\n{len(drifted)} claim(s) in CLAUDE.md no longer match the repo.")
        print("Fix the code or fix the file — a stale CLAUDE.md misleads every session.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
