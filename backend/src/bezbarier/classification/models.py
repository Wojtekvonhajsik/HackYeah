"""Wspólny model danych: cechy, obserwacje, źródła.

Obserwacja = jedna informacja o jednej cesze (np. "krawężnik 8-15 cm") z jednego źródła.
Nie zależy od profilu użytkownika - profil wchodzi dopiero w klasyfikatorze.
"""

from __future__ import annotations

import math
from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class FeatureType(str, Enum):
    """Taksonomia cech. Nazwy atrybutów zgodne (gdzie się da) z tagami OpenStreetMap."""

    KERB = "kerb"              # attrs: kind (raised|lowered|flush), height_cm
    STEPS = "steps"            # attrs: count, step_height_cm, handrail, contrast_marking, ramp, elevator
    RAMP = "ramp"              # attrs: incline_pct, width_cm
    ENTRANCE = "entrance"      # attrs: width_cm, threshold_cm, automatic_door
    SURFACE = "surface"        # attrs: value (wartości OSM: asphalt, sett, cobblestone, gravel...)
    PATH_WIDTH = "path_width"  # attrs: width_cm
    INCLINE = "incline"        # attrs: incline_pct
    OBSTACLE = "obstacle"      # attrs: kind, blocks_path, remaining_width_cm, height_from_ground_cm, temporary
    CROSSING = "crossing"      # attrs: kerb, kerb_height_cm, tactile_paving, traffic_signals, sound_signals
    AMENITY = "amenity"        # attrs: kind (bench|toilets_wheelchair|elevator)


class Range(BaseModel):
    """Wartość liczbowa z niepewnością. AI na zdjęciu 2D nie zmierzy dokładnie, więc zakres."""

    lo: float
    hi: float

    @model_validator(mode="after")
    def _check_order(self) -> Range:
        if self.lo > self.hi:
            raise ValueError(f"lo ({self.lo}) > hi ({self.hi})")
        return self

    @classmethod
    def exact(cls, value: float) -> Range:
        return cls(lo=value, hi=value)

    @classmethod
    def parse(cls, raw: Any) -> Range | None:
        """Akceptuje liczbę, {"lo":..,"hi":..} albo None."""
        if raw is None:
            return None
        if isinstance(raw, Range):
            return raw
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return cls.exact(float(raw))
        if isinstance(raw, dict):
            return cls(**raw)
        raise ValueError(f"Nie da się sparsować zakresu: {raw!r}")

    def __str__(self) -> str:
        if self.hi == math.inf:
            return f"≥{self.lo:g}"
        if self.lo == self.hi:
            return f"{self.lo:g}"
        return f"{self.lo:g}-{self.hi:g}"


class SourceType(str, Enum):
    OFFICIAL = "official"            # otwarte dane miasta (otwartedane.um.krakow.pl, MSIP)
    OWNER = "owner"                  # właściciel obiektu
    VERIFIED_USER = "verified_user"  # zgłoszenie potwierdzone przez innych / moderatora
    OSM = "osm"                      # OpenStreetMap (dane społecznościowe)
    USER_REPORT = "user_report"      # pojedyncze zgłoszenie użytkownika
    AI_DETECTION = "ai_detection"    # automatyczna detekcja ze zdjęcia


class Source(BaseModel):
    type: SourceType
    name: str                       # np. "Mapillary, zdjęcie 123", "OpenStreetMap node/456"
    url: str | None = None
    license: str | None = None      # np. "CC-BY-SA 4.0", "ODbL"
    observed_at: date               # kiedy stan był prawdziwy (data zdjęcia / ostatniego potwierdzenia)
    retrieved_at: date | None = None
    sample: bool = False            # dane przykładowe - wymaganie jury: wyraźnie oznaczyć


class GeoPoint(BaseModel):
    lat: float
    lon: float


class Observation(BaseModel):
    id: str
    type: FeatureType
    attrs: dict[str, Any] = Field(default_factory=dict)
    location: GeoPoint
    source: Source
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)  # pewność detektora; 1.0 dla danych spoza AI
    place_id: str | None = None     # jeśli cecha dotyczy konkretnego miejsca (np. wejście do kawiarni)


class Feature(BaseModel):
    """Jedna fizyczna cecha w terenie = zgrupowane obserwacje z różnych źródeł."""

    id: str
    type: FeatureType
    location: GeoPoint
    place_id: str | None = None
    observations: list[Observation]
