"""Skanowanie okolicy miejsca: zdjęcia (Mapillary) -> detektor -> obserwacje."""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel

from .classification.fusion import haversine_m
from .classification.models import FeatureType, GeoPoint, Observation
from .detection import Detector, ImageRef, detections_to_observations

# Wejście widoczne na zdjęciu przypisujemy do miejsca tylko, gdy kamera jest blisko i patrzy w jego stronę
ENTRANCE_MAX_DISTANCE_M = 25
ENTRANCE_MAX_ANGLE_DEG = 45

# Zdjęcia starsze niż to analizujemy tylko, gdy nowszych nie ma
MAX_IMAGE_AGE_DAYS = 3 * 365


class ScanResult(BaseModel):
    images_found: int
    radius_m: float | None = None  # promień, w którym ostatecznie szukano zdjęć
    images_analyzed: int
    images_skipped: int  # już przeanalizowane wcześniej (pomijane)
    observations: list[Observation]
    errors: list[str]
    notes: list[str] = []  # np. "brak nowych zdjęć - użyto starszych"


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


def select_images(
    images: list[ImageRef],
    target: GeoPoint,
    limit: int,
    today: date,
    already_analyzed: set[str],
) -> tuple[list[ImageRef], list[str]]:
    """Najbliższe jeszcze nieanalizowane zdjęcia, preferując nowe (nie starsze niż MAX_IMAGE_AGE_DAYS).

    Stare zdjęcia dawałyby od razu "nieaktualne" obserwacje - płacimy za nie tylko, gdy nowszych nie ma.
    """
    fresh = [im for im in images if _key(im) not in already_analyzed]
    recent = [im for im in fresh if (today - im.captured_at).days <= MAX_IMAGE_AGE_DAYS]
    notes = []
    pool = recent
    if not recent and fresh:
        pool = fresh
        notes.append(
            f"Brak nowych zdjęć z ostatnich {MAX_IMAGE_AGE_DAYS // 365} lat - użyto starszych, "
            "ich wyniki będą oznaczone jako nieaktualne."
        )
    selected = sorted(pool, key=lambda im: haversine_m(im.location, target))[:limit]
    return selected, notes


def _key(image: ImageRef) -> str:
    return f"{image.provider}-{image.id}"


def scan_images(
    place_id: str,
    place_location: GeoPoint,
    images: list[ImageRef],
    detector: Detector,
    already_analyzed: set[str],
    max_images: int = 5,
    today: date | None = None,
    link_beyond_m: float | None = None,
) -> ScanResult:
    """Każdy skan analizuje do max_images NOWYCH zdjęć - kolejny skan tego samego miejsca bierze następne.

    link_beyond_m: obserwacje ze zdjęć dalszych niż tyle od miejsca dostają near_place_id (okolica miejsca) -
    inaczej przy powiększonym promieniu skanu wypadłyby poza okolicę i nie trafiłyby do oceny.
    Do samego miejsca (place_id) przypisujemy tylko wejścia widoczne z kamery skierowanej na miejsce.
    """
    today = today or date.today()
    skipped = sum(_key(im) in already_analyzed for im in images)
    selected, notes = select_images(images, place_location, max_images, today, already_analyzed)
    observations: list[Observation] = []
    errors: list[str] = []
    analyzed = 0
    for image in selected:
        try:
            detections = detector.detect(image)
        except Exception as e:  # błąd jednego zdjęcia (limit API, timeout) nie przerywa skanu
            errors.append(f"zdjęcie {image.id}: {type(e).__name__}: {e}")
            continue
        already_analyzed.add(_key(image))
        analyzed += 1
        for obs in detections_to_observations(
            image, detections, detector.model_name, sample=detector.model_name.startswith("mock")
        ):
            if obs.type == FeatureType.ENTRANCE:
                if not faces(image, place_location):
                    continue  # wejście do innego budynku
                obs = obs.model_copy(update={"place_id": place_id})
            elif link_beyond_m is not None and haversine_m(image.location, place_location) > link_beyond_m:
                obs = obs.model_copy(update={"near_place_id": place_id})
            observations.append(obs)
    return ScanResult(
        images_found=len(images),
        images_analyzed=analyzed,
        images_skipped=skipped,
        observations=observations,
        errors=errors,
        notes=notes,
    )
