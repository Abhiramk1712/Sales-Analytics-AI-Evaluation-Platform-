import pytest

from backend.metrics import calculators


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
async def test_calculators_return_structured_payload_on_empty_data():
    db = FakeDB()

    revenue = await calculators.get_total_revenue(db)
    quota = await calculators.get_total_quota(db)
    attainment = await calculators.get_quota_attainment(db)
    win_rate = await calculators.get_win_rate(db)
    pipeline = await calculators.get_open_pipeline(db)

    assert revenue["value"] == 0.0
    assert quota["value"] == 0.0
    assert attainment["value"] == 0.0
    assert win_rate["value"] == 0.0
    assert pipeline["value"] == 0.0
    assert isinstance(win_rate["warnings"], list)


@pytest.mark.asyncio
async def test_unknown_filters_do_not_crash():
    db = FakeDB()
    result = await calculators.get_total_revenue(db, filters={"unknown": "x"})
    assert result["value"] == 0.0
    assert isinstance(result["warnings"], list)
