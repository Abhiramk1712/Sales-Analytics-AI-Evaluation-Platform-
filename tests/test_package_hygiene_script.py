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
