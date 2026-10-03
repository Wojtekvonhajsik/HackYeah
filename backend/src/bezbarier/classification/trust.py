"""Wiarygodność i aktualność obserwacji.

trust = waga_źródła x pewność_detektora x aktualność
aktualność = 0.5 ** (wiek / okres_półtrwania[typ cechy])

Po upływie okresu półtrwania obserwacja dostaje status "nieaktualne".
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from .models import FeatureType, Observation, SourceType

SOURCE_WEIGHT: dict[SourceType, float] = {
    SourceType.OFFICIAL: 0.95,
    SourceType.OWNER: 0.85,
    SourceType.VERIFIED_USER: 0.8,
    SourceType.OSM: 0.7,
    SourceType.USER_REPORT: 0.5,
    SourceType.AI_DETECTION: 0.4,
}

# Jak szybko informacja się starzeje. Krawężnik zmienia się rzadko, rusztowanie znika szybko.
HALF_LIFE_DAYS: dict[FeatureType, int] = {
    FeatureType.KERB: 730,
    FeatureType.STEPS: 1825,
    FeatureType.RAMP: 1825,
    FeatureType.ENTRANCE: 1095,
    FeatureType.SURFACE: 1095,
    FeatureType.PATH_WIDTH: 1095,
    FeatureType.INCLINE: 3650,
    FeatureType.OBSTACLE: 90,
    FeatureType.CROSSING: 730,
    FeatureType.AMENITY: 365,
}
TEMPORARY_HALF_LIFE_DAYS = 14

CONFIRMED_SOURCES = {SourceType.OFFICIAL, SourceType.OWNER, SourceType.VERIFIED_USER}


class DataStatus(str, Enum):
    CONFIRMED = "confirmed"      # źródło urzędowe / właściciel / zweryfikowane
    UNVERIFIED = "unverified"    # OSM, pojedyncze zgłoszenie, detekcja AI
    OUTDATED = "outdated"        # starsze niż okres półtrwania dla tego typu cechy
    CONFLICTING = "conflicting"  # źródła się nie zgadzają (ustawiane na poziomie cechy)


def half_life_days(obs: Observation) -> int:
    if obs.attrs.get("temporary"):
        return TEMPORARY_HALF_LIFE_DAYS
    return HALF_LIFE_DAYS[obs.type]


def freshness(obs: Observation, today: date) -> float:
    age_days = max(0, (today - obs.source.observed_at).days)
    return 0.5 ** (age_days / half_life_days(obs))


def trust_score(obs: Observation, today: date) -> float:
    return round(SOURCE_WEIGHT[obs.source.type] * obs.confidence * freshness(obs, today), 4)


def data_status(obs: Observation, today: date) -> DataStatus:
    if freshness(obs, today) < 0.5:
        return DataStatus.OUTDATED
    if obs.source.type in CONFIRMED_SOURCES:
        return DataStatus.CONFIRMED
    return DataStatus.UNVERIFIED
