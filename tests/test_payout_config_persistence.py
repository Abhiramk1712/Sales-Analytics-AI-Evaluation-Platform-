"""
tests/test_payout_config_persistence.py
========================================
Verify the PayoutConfiguration model can be instantiated and has required fields.
"""
import uuid
from datetime import date, datetime

from backend.models import PayoutConfiguration


def test_payout_config_model_has_required_fields():
    """PayoutConfiguration model should have all required columns."""
    config = PayoutConfiguration(
        id=uuid.uuid4(),
        company_id="test-company",
        version=1,
        config_json={
            "tiers": [
                {"min_attainment_pct": 0, "max_attainment_pct": 80, "rate": 0.03},
                {"min_attainment_pct": 80, "max_attainment_pct": 100, "rate": 0.05},
            ],
            "accelerator_rate": 0.02,
            "team_bonus": 2000.0,
        },
        effective_date=date.today(),
        is_active=True,
    )
    assert config.company_id == "test-company"
    assert config.version == 1
    assert isinstance(config.config_json, dict)
    assert len(config.config_json["tiers"]) == 2


def test_payout_config_default_values():
    """PayoutConfiguration should have company_id and config_json set."""
    config = PayoutConfiguration(
        company_id="demo-co",
        config_json={"tiers": []},
        is_active=True,
    )
    assert config.company_id == "demo-co"
    assert config.config_json == {"tiers": []}


def test_job_status_model_exists():
    """JobStatus model should be importable."""
    from backend.models import JobStatus
    job = JobStatus(
        job_type="ingestion",
        status="queued",
    )
    assert job.job_type == "ingestion"
    assert job.status == "queued"
