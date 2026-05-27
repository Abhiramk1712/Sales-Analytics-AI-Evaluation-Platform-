"""
backend/ml/training_pipeline.py
================================
Safe ML training pipeline — separate from inference
"""
from typing import Optional, Dict, Any
from backend.config import settings


class TrainingPipeline:
    """
    Manages safe model training with proper workflow separation.
    
    Ensures:
    - Training happens separately from inference
    - Models are not trained inside request handlers (except demo mode)
    - Training is properly logged and versioned
    """
    
    def __init__(self, demo_mode: bool = False):
        """
        Initialize training pipeline.
        
        Args:
            demo_mode: If True, allow training in request handlers
                      Set False in production
        """
        self.demo_mode = demo_mode
        self.training_history = []
    
    def can_train_in_request(self) -> bool:
        """
        Check if we should allow training inside a request handler.
        
        Returns:
            True only if demo_mode is enabled
        """
        if not self.demo_mode:
            return False
        return True
    
    def register_training_run(
        self,
        model_name: str,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log a training run.
        
        Args:
            model_name: Name of trained model
            status: 'success', 'warning', 'error'
            details: Additional metadata
        """
        entry = {
            "model_name": model_name,
            "status": status,
            "details": details or {},
        }
        self.training_history.append(entry)


# Global training pipeline (environment-configurable)
_global_pipeline = TrainingPipeline(demo_mode=settings.TRAINING_DEMO_MODE)


def get_training_pipeline() -> TrainingPipeline:
    """Get the global training pipeline."""
    return _global_pipeline
