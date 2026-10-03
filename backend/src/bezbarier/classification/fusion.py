"""Łączenie obserwacji z różnych źródeł w cechy (jedna fizyczna rzecz w terenie)."""

from __future__ import annotations

import math

from .models import Feature, FeatureType, GeoPoint, Observation

DEFAULT_RADIUS_M = 4.0

# Cechy przypisane do miejsca łączymy po place_id niezależnie od odległości.
# Uproszczenie prototypu: jedno wejście na miejsce.
PLACE_SCOPED_TYPES = {FeatureType.ENTRANCE}

# Dla tych typów obserwacje muszą mieć ten sam attrs["kind"] (ławka != toaleta).
KIND_SCOPED_TYPES = {FeatureType.AMENITY}


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    r = 6_371_000
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi = phi2 - phi1
    dlmb = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _same_feature(f: Feature, obs: Observation, radius_m: float) -> bool:
    if f.type != obs.type:
        return False
    if obs.type in KIND_SCOPED_TYPES and f.observations[0].attrs.get("kind") != obs.attrs.get("kind"):
        return False
    if obs.type in PLACE_SCOPED_TYPES:
        if f.place_id != obs.place_id:
            return False
        if obs.place_id is not None:
            return True
    # Pozostałe cechy (krawężnik, nawierzchnia...) łączymy po odległości, niezależnie od place_id -
    # np. nawierzchnia z OSM i ze zdjęcia przypisanego do miejsca to ta sama rzecz
    return haversine_m(f.location, obs.location) <= radius_m


def group_observations(observations: list[Observation], radius_m: float = DEFAULT_RADIUS_M) -> list[Feature]:
    features: list[Feature] = []
    for obs in observations:
        match = next((f for f in features if _same_feature(f, obs, radius_m)), None)
        if match is not None:
            match.observations.append(obs)
        else:
            features.append(Feature(
                id=f"feat-{obs.id}",
                type=obs.type,
                location=obs.location,
                place_id=obs.place_id,
                observations=[obs],
            ))
    return features
