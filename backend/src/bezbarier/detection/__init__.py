import os

from .base import Detection, Detector, ImageRef, detections_to_observations
from .mock import MockDetector

__all__ = ["Detection", "Detector", "ImageRef", "MockDetector", "detections_to_observations", "get_detector"]


def get_detector() -> Detector:
    """DETECTOR=claude -> model wizyjny Claude (wymaga `pip install .[vlm]` i klucza API), inaczej mock."""
    if os.environ.get("DETECTOR") == "claude":
        from .claude_vlm import ClaudeVisionDetector

        return ClaudeVisionDetector()
    return MockDetector()
