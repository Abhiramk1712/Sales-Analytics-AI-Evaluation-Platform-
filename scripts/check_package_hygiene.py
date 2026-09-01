#!/usr/bin/env python3
"""
scripts/check_package_hygiene.py
=================================
Validates that the repo/package does not contain forbidden artifacts.

Usage:
    python scripts/check_package_hygiene.py [--path <root>]

Exit codes:
    0  — all checks pass
    1  — forbidden artifacts found (do not share/distribute)
"""
from __future__ import annotations

import sys
import os
import argparse
from pathlib import Path

FORBIDDEN_SECRET_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.test",
}

FORBIDDEN_DIRS = {
    "venv",
    ".venv",
    "env",
    "node_modules",
    "__MACOSX",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

# Project-specific forbidden paths (relative to repo root).
#
# `companies/` is deliberately absent. It was listed here when the demo datasets
# were treated as generated output, but the platform resolves
# DEMO_DEFAULT_COMPANY against that folder at runtime — without it every
# company-scoped route returns 404, so it is source, not a build artifact.
# `companies/_uploads/` stays forbidden: that is where the ingestion endpoint
# writes caller-supplied files, so it is both generated and arbitrary content.
FORBIDDEN_RELATIVE_DIRS = {
    "backend/ml/saved",
    "frontend/dist",
    "companies/_uploads",
}

FORBIDDEN_EXTENSIONS = {
    ".pyc",
    ".pkl",
    ".joblib",
    ".zip",
    ".pt",
    ".onnx",
}

FORBIDDEN_OS_FILES = {
    ".DS_Store",
    "Thumbs.db",
}

# Directories to skip regardless of hygiene status
SKIP_DIRS = {
    ".git",
}

SKIP_RELATIVE_DIRS = {
    "dist/packages",
}


def _is_forbidden_secret_file(filename: str) -> bool:
    # Allow template file to remain in source control.
    if filename == ".env.example":
        return False

    if filename in FORBIDDEN_SECRET_FILES:
        return True

    # Catch additional variants such as .env.staging, .env.prod, etc.
    if filename.startswith(".env."):
        return True

    return False


def check_hygiene(root: Path) -> list[str]:
    violations: set[str] = set()

    if not root.exists():
        return [f"INVALID ROOT: {root}"]

    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(root).as_posix() if dp != root else ""

        if rel_dir in SKIP_RELATIVE_DIRS:
            dirnames[:] = []
            continue

        if rel_dir in FORBIDDEN_RELATIVE_DIRS:
            violations.add(f"FORBIDDEN DIR: {rel_dir}/")

        # Detect forbidden directories recursively, then prune them from traversal.
        forbidden_children = [d for d in dirnames if d in FORBIDDEN_DIRS]
        for d in forbidden_children:
            rel = (dp / d).relative_to(root)
            violations.add(f"FORBIDDEN DIR: {rel}/")

        for d in dirnames:
            child_rel = (dp / d).relative_to(root).as_posix()
            if child_rel in FORBIDDEN_RELATIVE_DIRS:
                violations.add(f"FORBIDDEN DIR: {child_rel}/")

        dirnames[:] = [
            d
            for d in dirnames
            if d not in FORBIDDEN_DIRS
            and d not in SKIP_DIRS
            and (dp / d).relative_to(root).as_posix() not in FORBIDDEN_RELATIVE_DIRS
            and (dp / d).relative_to(root).as_posix() not in SKIP_RELATIVE_DIRS
        ]

        for fname in filenames:
            fp = dp / fname
            rel = fp.relative_to(root)
            suffix = fp.suffix.lower()

            if _is_forbidden_secret_file(fname):
                violations.add(f"FORBIDDEN SECRET FILE: {rel}")

            if fname in FORBIDDEN_OS_FILES:
                violations.add(f"FORBIDDEN OS FILE: {rel}")

            if suffix in FORBIDDEN_EXTENSIONS:
                if suffix == ".zip":
                    violations.add(f"FORBIDDEN ZIP: {rel}")
                else:
                    violations.add(f"FORBIDDEN ARTIFACT: {rel}")

    return sorted(violations)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check repo/package hygiene.")
    parser.add_argument(
        "--path",
        default=str(Path(__file__).parent.parent),
        help="Root path to check (default: repo root)",
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    print(f"Checking hygiene in: {root}\n")

    violations = check_hygiene(root)

    if not violations:
        print("✓ All hygiene checks passed. Safe to package and share.\n")
        sys.exit(0)
    else:
        print(f"✗ Found {len(violations)} hygiene violation(s):\n")
        for v in violations:
            print(f"  • {v}")
        print("\nFix these before packaging or sharing this repo.")
        print("Run: bash scripts/package_clean.sh to create a clean zip.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
