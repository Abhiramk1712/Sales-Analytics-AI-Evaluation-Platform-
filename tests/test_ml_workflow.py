"""
tests/test_ml_workflow.py
=======================
Tests for ML workflow safety and structure
"""
import pytest
from backend.ml.model_registry import ModelRun
from backend.ml.training_pipeline import get_training_pipeline
from backend.ml.evaluation import classification_metrics, clustering_summary
import numpy as np


class TestModelRegistry:
    def test_model_run_creation(self):
        run = ModelRun(
            model_name="deal_scorer",
            model_version="1.0",
            feature_names=["amount", "stage"],
            target_name="won",
            metrics={"auc": 0.82, "accuracy": 0.75}
        )
        assert run.model_name == "deal_scorer"
        assert run.feature_names == ["amount", "stage"]
        assert run.metrics["auc"] == 0.82

    def test_model_run_summary(self):
        run = ModelRun(
            model_name="forecaster",
            model_version="1.1",
            feature_names=["lag_1", "lag_12"],
            target_name="revenue",
            metrics={"rmse": 2500, "mae": 1800}
        )
        summary = run.summary()
        assert summary["model_name"] == "forecaster"
        assert summary["feature_count"] == 2


class TestTrainingPipeline:
    def test_pipeline_demo_mode(self):
        pipeline = get_training_pipeline()
        # Pipeline is initialized in demo mode
        assert pipeline.demo_mode == True
        assert pipeline.can_train_in_request() == True

    def test_register_training_run(self):
        pipeline = get_training_pipeline()
        pipeline.register_training_run(
            "test_model",
            "success",
            {"epochs": 10}
        )
        assert len(pipeline.training_history) > 0


class TestEvaluation:
    def test_classification_metrics(self):
        y_true = np.array([0, 1, 1, 0, 1])
        y_pred = np.array([0, 1, 1, 0, 0])
        metrics = classification_metrics(y_true, y_pred)
        assert "accuracy" in metrics
        assert "precision" in metrics
        assert "recall" in metrics
        assert "f1" in metrics

    def test_clustering_summary(self):
        labels = np.array([0, 0, 1, 1, 2])
        summary = clustering_summary(labels)
        assert summary["cluster_count"] == 3
        assert len(summary["cluster_sizes"]) == 3
