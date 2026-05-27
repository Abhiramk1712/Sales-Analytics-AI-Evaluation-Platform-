import pytest

from backend.features.build_features import (
    build_account_features,
    build_deal_features,
    build_pipeline_snapshot_features,
    build_rep_month_features,
)


class _Result:
    def __init__(self, scalar_value=0, rows=None):
        self._scalar = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return None


class FakeDB:
    async def execute(self, _query):
        return _Result(0, [])


@pytest.mark.asyncio
async def test_feature_builders_return_dataframes_and_warnings():
    db = FakeDB()
    rep = await build_rep_month_features(db)
    deal = await build_deal_features(db)
    acct = await build_account_features(db)
    pipe = await build_pipeline_snapshot_features(db)

    assert "data" in rep and "warnings" in rep
    assert "data" in deal and "warnings" in deal
    assert "data" in acct and "warnings" in acct
    assert "data" in pipe and "warnings" in pipe
