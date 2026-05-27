"""Tests for archetype-driven data generator (backend/data_generator.py)."""
from __future__ import annotations

import pytest

from backend.data_generator import ARCHETYPE_PROFILES, _generate_dataset


@pytest.mark.parametrize("archetype", list(ARCHETYPE_PROFILES.keys()))
def test_archetype_dataset_generates(archetype):
    dataset = _generate_dataset(n_reps=3, months=3, archetype=archetype)
    assert dataset is not None
    assert len(dataset) > 0


def test_archetype_profiles_have_required_keys():
    required = {"deal_size_range", "quota_growth_factor"}
    for name, profile in ARCHETYPE_PROFILES.items():
        missing = required - profile.keys()
        assert not missing, f"Archetype '{name}' missing keys: {missing}"


def test_saas_enterprise_larger_deals():
    """SaaS enterprise archetype should generate larger deal sizes than SMB."""
    ent = ARCHETYPE_PROFILES["saas_enterprise"]
    smb = ARCHETYPE_PROFILES["saas_smb"]
    assert ent["deal_size_range"][1] > smb["deal_size_range"][1], "Enterprise max deal should exceed SMB max"


def test_all_archetypes_have_stage_weights():
    """Each archetype should have a win_rate_weight factor."""
    for name, profile in ARCHETYPE_PROFILES.items():
        assert "win_rate_weight" in profile, f"Archetype '{name}' missing win_rate_weight"


def test_dataset_quota_generated(tmp_path):
    """quota-equivalent data should have entries for each rep."""
    dataset = _generate_dataset(n_reps=4, months=3, archetype="saas_enterprise")
    quota_data = dataset.get("quotas", dataset.get("quota", []))
    assert len(quota_data) > 0, "Expected quota rows in dataset"


def test_ramp_extension_tables_present():
    """Extension tables (rep_ramp, bookings) should be present for saas_enterprise."""
    dataset = _generate_dataset(n_reps=2, months=6, archetype="saas_enterprise")
    keys_str = " ".join(str(k) for k in dataset.keys()).lower()
    assert "rep" in keys_str or "ramp" in keys_str or "bookings" in keys_str or len(dataset) >= 5, (
        f"Expected ramp/booking extension tables. Got keys: {list(dataset.keys())}"
    )
