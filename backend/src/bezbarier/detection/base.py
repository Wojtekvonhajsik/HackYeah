"""Interfejs detektora. Detektor NIE zna profilu użytkownika - wykrywa wszystko,
a wynik (obserwacje) jest liczony raz na zdjęcie i współdzielony przez wszystkich.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..classification.models import FeatureType, GeoPoint, Observation, Source, SourceType


class ImageRef(BaseModel):
    id: str
    provider: str                      # "mapillary", "user_upload", ...
    url: str | None = None
    location: GeoPoint                 # pozycja kamery
    heading: float | None = None       # kierunek kamery w stopniach
    captured_at: date
    license: str | None = None
    attribution_url: str | None = None


class Detection(BaseModel):
    type: FeatureType
    attrs: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class Detector(Protocol):
    model_name: str

    def detect(self, image: ImageRef, image_bytes: bytes | None = None) -> list[Detection]: ...


def detections_to_observations(
    image: ImageRef,
    detections: list[Detection],
    model_name: str,
    place_id: str | None = None,
    sample: bool = False,
) -> list[Observation]:
    source = Source(
        type=SourceType.AI_DETECTION,
        name=f"{image.provider} {image.id} (detekcja: {model_name})",
        url=image.attribution_url or image.url,
        license=image.license,
        observed_at=image.captured_at,  # stan z dnia zrobienia zdjęcia, nie z dnia analizy
        retrieved_at=date.today(),
        sample=sample,
    )
    # TODO: przesunąć lokalizację o kilka metrów wg heading zamiast pozycji kamery
    return [
        Observation(
            id=f"{image.provider}-{image.id}-{i}",
            type=d.type,
            attrs=d.attrs,
            location=image.location,
            source=source,
            confidence=d.confidence,
            place_id=place_id,
        )
        for i, d in enumerate(detections)
    ]
