from pathlib import Path
import json

from backend.data_generator import (
    TABLE_ORDER,
    _build_revops_reconciliation_snapshot,
    _collect_csv_ingestion_runs,
    _generate_dataset,
    _safe_company_dir_name,
    _write_dataset_to_csv,
    _write_ingestion_audit,
)


def test_safe_company_dir_name():
    assert _safe_company_dir_name("Acme Corp") == "acme-corp"
    assert _safe_company_dir_name("  ") == "default-company"


def test_write_dataset_to_csv_creates_expected_files(tmp_path: Path):
    # Use enough deals + months so at least some close within the recognition window
    dataset = _generate_dataset(n_reps=4, n_accounts=10, n_deals=40, months=6)

    company_dir = _write_dataset_to_csv(
        dataset=dataset,
        company_name="Acme Corp",
        base_dir=str(tmp_path),
    )

    assert company_dir == tmp_path / "acme-corp"

    for table_name in TABLE_ORDER:
        csv_path = company_dir / f"{table_name}.csv"
        assert csv_path.exists(), f"Missing CSV file: {csv_path}"
        assert csv_path.stat().st_size > 0, f"CSV file is empty: {csv_path}"


def test_ingestion_audit_written_for_company_dataset(tmp_path: Path):
    dataset = _generate_dataset(n_reps=2, n_accounts=3, n_deals=4, months=2)
    company_dir = _write_dataset_to_csv(dataset=dataset, company_name="Acme Corp", base_dir=str(tmp_path))

    runs = _collect_csv_ingestion_runs(company_dir)
    db_counts = {table: len(dataset[table]) for table in TABLE_ORDER}
    audit_path = _write_ingestion_audit(company_dir=company_dir, company_name="Acme Corp", db_counts=db_counts, runs=runs)

    assert audit_path.exists()
    payload = json.loads(audit_path.read_text(encoding="utf-8"))

    assert payload["company_name"] == "Acme Corp"
    assert payload["status"] == "success"
    assert payload["db_rows_loaded"]["deals"] == len(dataset["deals"])
    assert len(payload["sources"]) == len(TABLE_ORDER)


def test_reconciliation_rep_outlier_threshold_is_configurable():
    dataset = {
        "deals": [{"stage": "Closed Won", "amount": "100"}],
        "quotas": [{"rep_id": "R1", "amount": "300", "period": "2026-Q1"}],
        "revenue": [{"rep_id": "R1", "amount": "100", "period": "2026-01"}],
        "reps": [{"id": "R1", "team_id": "T1"}],
        "teams": [{"id": "T1", "name": "West Sales Team"}],
    }
    extension_tables = {
        "bookings": [{"amount": "100"}],
    }

    relaxed = _build_revops_reconciliation_snapshot(
        dataset,
        extension_tables,
        rep_quota_revenue_outlier_threshold=5.0,
    )
    strict = _build_revops_reconciliation_snapshot(
        dataset,
        extension_tables,
        rep_quota_revenue_outlier_threshold=2.0,
    )

    assert relaxed["rep_ratio_outliers"] == 0
    assert strict["rep_ratio_outliers"] == 1
    assert strict["rep_ratio_outliers_gt5x"] == 0
