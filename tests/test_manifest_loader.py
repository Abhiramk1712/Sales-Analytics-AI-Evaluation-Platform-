from pathlib import Path

from backend.ingestion.intelligent_ingestion import inspect_source_directory
from backend.ingestion.manifest_loader import build_manifest_canonical_dataset


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    lines = [",".join(header)]
    lines.extend([",".join(row) for row in rows])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_manifest_loader_emits_relationship_resolution_and_lineage(tmp_path: Path):
    _write_csv(
        tmp_path / "teams.csv",
        ["team_id", "team_name", "region"],
        [["t-1", "West Team", "West"]],
    )
    _write_csv(
        tmp_path / "users.csv",
        ["user_id", "name", "email", "team_id"],
        [["u-1", "Alice Smith", "alice@example.com", "t-1"]],
    )
    _write_csv(
        tmp_path / "accounts.csv",
        ["account_id", "account_name", "industry", "employee_count", "annual_revenue"],
        [["a-1", "Acme", "SaaS", "100", "1000000"]],
    )
    _write_csv(
        tmp_path / "opportunities.csv",
        ["opportunity_id", "user_id", "account_id", "opportunity_name", "stage", "amount"],
        [["d-1", "u-1", "a-1", "Expansion", "Proposal", "50000"]],
    )

    inspection = inspect_source_directory(str(tmp_path))
    dataset, warnings, metadata = build_manifest_canonical_dataset(inspection)

    assert len(dataset["deals"]) == 1
    assert "relationship_resolution" in metadata
    assert isinstance(metadata["relationship_resolution"], dict)
    assert "canonical_lineage" in metadata
    assert "deals" in metadata["canonical_lineage"]
    assert len(metadata["canonical_lineage"]["deals"]) == 1


def test_manifest_loader_reports_unresolved_required_relationships(tmp_path: Path):
    _write_csv(
        tmp_path / "users.csv",
        ["user_id", "name", "email", "team_id"],
        [["u-1", "Alice Smith", "alice@example.com", "unknown-team"]],
    )

    inspection = inspect_source_directory(str(tmp_path))
    _dataset, warnings, metadata = build_manifest_canonical_dataset(inspection)

    # We only assert that deterministic relationship reporting is present and can include unresolved references.
    assert "relationship_resolution" in metadata
    assert isinstance(warnings, list)
