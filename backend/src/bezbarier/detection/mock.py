"""Detektor zwracający stałe wyniki - do dema i testów bez klucza API."""

from __future__ import annotations

from ..classification.models import FeatureType
from .base import Detection, ImageRef


class MockDetector:
    model_name = "mock-v0"

    def detect(self, image: ImageRef, image_bytes: bytes | None = None) -> list[Detection]:
        return [
            Detection(type=FeatureType.KERB, attrs={"kind": "raised", "height_cm": {"lo": 8, "hi": 15}}, confidence=0.8),
            Detection(type=FeatureType.SURFACE, attrs={"value": "sett"}, confidence=0.9),
        ]
