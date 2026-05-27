import pytest

from backend.routers import forecasting as forecasting_router


class _ScalarResult:
    def __init__(self, latest=None):
        self._latest = latest

    def scalars(self):
        return self

    def first(self):
        return self._latest


class _FakeRow:
    def __init__(self, prediction):
        self.prediction = prediction


class _FakeDB:
    def __init__(self, latest=None):
        self.latest = latest
        self.added = []

    async def execute(self, _query):
        return _ScalarResult(self.latest)

    def add(self, obj):
        self.added.append(obj)


@pytest.mark.asyncio
async def test_persist_prediction_inserts_when_new():
    db = _FakeDB(latest=None)
    inserted = await forecasting_router._persist_prediction(
        db=db,
        model_name="revenue_forecast",
        entity_type="company",
        prediction={"forecast_values": [1, 2, 3]},
        model_version="v1",
        confidence=0.7,
        entity_id=None,
    )
    assert inserted is True
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_persist_prediction_dedupes_latest_identical_payload():
    db = _FakeDB(latest=_FakeRow(prediction={"forecast_values": [1, 2, 3]}))
    inserted = await forecasting_router._persist_prediction(
        db=db,
        model_name="revenue_forecast",
        entity_type="company",
        prediction={"forecast_values": [1, 2, 3]},
        model_version="v1",
        confidence=0.7,
        entity_id=None,
    )
    assert inserted is False
    assert len(db.added) == 0
