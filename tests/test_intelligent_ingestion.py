from pathlib import Path

from backend.ingestion.intelligent_ingestion import build_canonical_dataset, inspect_source_directory


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    lines = [",".join(header)]
    lines.extend([",".join(row) for row in rows])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_inspect_source_directory_infers_entities(tmp_path: Path):
    _write_csv(
        tmp_path / "sales_people.csv",
        ["salesperson_name", "salesperson_email", "territory"],
        [["Alice Smith", "alice@example.com", "West"]],
    )
    _write_csv(
        tmp_path / "accounts_master.csv",
        ["company_name", "vertical", "employees", "arr"],
        [["Acme Inc", "SaaS", "500", "20000000"]],
    )
    _write_csv(
        tmp_path / "opportunities.csv",
        ["opp_name", "customer_name", "owner_email", "pipeline_stage", "deal_value", "win_probability", "created_date"],
        [["Platform Expansion", "Acme Inc", "alice@example.com", "Proposal", "120000", "55", "2026-03-01"]],
    )

    inspection = inspect_source_directory(str(tmp_path))
    entities = {f["entity"] for f in inspection["files"]}

    assert "reps" in entities
    assert "accounts" in entities
    assert "deals" in entities
    assert len(inspection["source_manifest"]) == 3


def test_build_canonical_dataset_adds_required_features(tmp_path: Path):
    _write_csv(
        tmp_path / "sales_people.csv",
        ["salesperson_name", "salesperson_email", "territory"],
        [["Alice Smith", "alice@example.com", "West"]],
    )
    _write_csv(
        tmp_path / "opportunities.csv",
        ["opp_name", "customer_name", "owner_email", "pipeline_stage", "deal_value", "created_date"],
        [["Platform Expansion", "Acme Inc", "alice@example.com", "Proposal", "120000", "2026-03-01"]],
    )

    inspection = inspect_source_directory(str(tmp_path))
    dataset, warnings = build_canonical_dataset(inspection)

    assert len(dataset["teams"]) >= 1
    assert len(dataset["reps"]) == 1
    assert len(dataset["deals"]) == 1
    # No quotas/revenue source supplied, defaults should be generated.
    assert len(dataset["quotas"]) >= 1
    assert len(dataset["revenue"]) >= 1
    assert any("default" in w.lower() or "no quota source" in w.lower() for w in warnings)


def test_inspect_source_directory_supports_pdf_preview(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "bookings.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    def fake_preview_rows(_path: Path, limit: int = 200):
        return [
            {"owner_email": "alice@example.com", "revenue_month": "2026-04", "bookings": "25000"},
            {"owner_email": "bob@example.com", "revenue_month": "2026-04", "bookings": "18000"},
        ][:limit]

    monkeypatch.setattr("backend.ingestion.intelligent_ingestion._load_pdf_preview_rows", fake_preview_rows)

    inspection = inspect_source_directory(str(tmp_path))

    assert len(inspection["files"]) == 1
    assert inspection["files"][0]["source_type"] == "pdf"
    assert inspection["files"][0]["entity"] == "revenue"
    assert inspection["source_manifest"][0]["source_type"] == "pdf"
