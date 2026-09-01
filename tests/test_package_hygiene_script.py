from __future__ import annotations

from pathlib import Path

from scripts.check_package_hygiene import check_hygiene


def test_hygiene_flags_forbidden_files_and_dirs(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "index.html").write_text("demo", encoding="utf-8")

    violations = check_hygiene(tmp_path)

    assert any("FORBIDDEN SECRET FILE" in v for v in violations)
    assert any(".venv" in v for v in violations)
    assert any("frontend/dist" in v for v in violations)


def test_hygiene_allows_the_committed_demo_datasets(tmp_path: Path) -> None:
    """
    `companies/` is source, not a build artifact.

    It was on the forbidden list while the demo data was treated as generated
    output. But DEMO_DEFAULT_COMPANY resolves against that folder at runtime, so
    a checkout without it returns 404 on every company-scoped route — which is
    the state the published repository was actually in.
    """
    company = tmp_path / "companies" / "techo-solutions"
    company.mkdir(parents=True)
    (company / "reps.csv").write_text("id,name\n1,A\n", encoding="utf-8")

    violations = check_hygiene(tmp_path)

    assert not any("companies" in v for v in violations), violations


def test_hygiene_still_flags_the_ingestion_upload_scratch(tmp_path: Path) -> None:
    """
    `companies/_uploads/` is where the ingestion endpoint writes caller-supplied
    files. Generated, arbitrary content, and never safe to package — so relaxing
    the rule on `companies/` must not relax it on this.
    """
    uploads = tmp_path / "companies" / "_uploads" / "ingestion-abc123"
    uploads.mkdir(parents=True)
    (uploads / "whatever.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    violations = check_hygiene(tmp_path)

    assert any("companies/_uploads" in v for v in violations), violations
