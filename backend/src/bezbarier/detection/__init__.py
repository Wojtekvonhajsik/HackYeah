import os

from .base import Detection, Detector, ImageRef, detections_to_observations
from .mock import MockDetector

__all__ = ["Detection", "Detector", "ImageRef", "MockDetector", "detections_to_observations", "get_detector"]


def get_detector() -> Detector:
    """DETECTOR=gemini | claude | mock (domyślnie mock - stałe wyniki, bez klucza API)."""
    kind = os.environ.get("DETECTOR", "mock")
    if kind == "gemini":
        from .gemini_vlm import GeminiVisionDetector

        return GeminiVisionDetector()
    if kind == "claude":
        from .claude_vlm import ClaudeVisionDetector

        return ClaudeVisionDetector()
    return MockDetector()
