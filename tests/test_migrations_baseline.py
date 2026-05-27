from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "migrations" / "versions"


def test_migrations_versions_contains_baseline_revision():
    files = [p for p in VERSIONS.glob("*.py") if p.name != "__init__.py"]
    assert files, "Expected at least one Alembic revision file"


def test_baseline_revision_has_upgrade_and_downgrade():
    baseline = VERSIONS / "20260505_0001_phase9_baseline.py"
    assert baseline.exists()
    content = baseline.read_text(encoding="utf-8")
    assert "def upgrade()" in content
    assert "def downgrade()" in content
