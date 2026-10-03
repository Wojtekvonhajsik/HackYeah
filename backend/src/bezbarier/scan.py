"""Skanowanie okolicy miejsca: zdjęcia (Mapillary) -> detektor -> obserwacje."""

from __future__ import annotations

import math

from pydantic import BaseModel

from .classification.fusion import haversine_m
from .classification.models import FeatureType, GeoPoint, Observation
from .detection import Detector, ImageRef, detections_to_observations

# Wejście widoczne na zdjęciu przypisujemy do miejsca tylko, gdy kamera jest blisko i patrzy w jego stronę
ENTRANCE_MAX_DISTANCE_M = 25
ENTRANCE_MAX_ANGLE_DEG = 45


class ScanResult(BaseModel):
    images_found: int
    radius_m: float | None = None  # promień, w którym ostatecznie szukano zdjęć
    images_analyzed: int
    images_skipped: int  # już przeanalizowane wcześniej
    observations: list[Observation]
    errors: list[str]


def bearing_deg(a: GeoPoint, b: GeoPoint) -> float:
    """Azymut z punktu a do b (0 = północ, 90 = wschód)."""
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dlmb = math.radians(b.lon - a.lon)
    x = math.sin(dlmb) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    return math.degrees(math.atan2(x, y)) % 360


def faces(image: ImageRef, target: GeoPoint) -> bool:
    if image.heading is None or haversine_m(image.location, target) > ENTRANCE_MAX_DISTANCE_M:
        return False
    diff = abs((bearing_deg(image.location, target) - image.heading + 180) % 360 - 180)
    return diff <= ENTRANCE_MAX_ANGLE_DEG


def nearest_images(images: list[ImageRef], target: GeoPoint, limit: int) -> list[ImageRef]:
    return sorted(images, key=lambda im: haversine_m(im.location, target))[:limit]


def scan_images(
    place_id: str,
    place_location: GeoPoint,
    images: list[ImageRef],
    detector: Detector,
    already_analyzed: set[str],
    max_images: int = 5,
) -> ScanResult:
    selected = nearest_images(images, place_location, max_images)
    observations: list[Observation] = []
    errors: list[str] = []
    analyzed = skipped = 0
    for image in selected:
        key = f"{image.provider}-{image.id}"
        if key in already_analyzed:
            skipped += 1
            continue
        try:
            detections = detector.detect(image)
        except Exception as e:  # błąd jednego zdjęcia (limit API, timeout) nie przerywa skanu
            errors.append(f"zdjęcie {image.id}: {type(e).__name__}: {e}")
            continue
        already_analyzed.add(key)
        analyzed += 1
        for obs in detections_to_observations(
            image, detections, detector.model_name, sample=detector.model_name.startswith("mock")
        ):
            if obs.type == FeatureType.ENTRANCE:
                if not faces(image, place_location):
                    continue  # wejście do innego budynku
                obs = obs.model_copy(update={"place_id": place_id})
            observations.append(obs)
    return ScanResult(
        images_found=len(images),
        images_analyzed=analyzed,
        images_skipped=skipped,
        observations=observations,
        errors=errors,
    )
