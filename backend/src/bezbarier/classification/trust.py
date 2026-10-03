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


# Tyle potwierdzeń (i co najmniej 2x więcej niż zaprzeczeń) = dane zweryfikowane przez społeczność
VERIFY_MIN_CONFIRMATIONS = 3


def half_life_days(obs: Observation) -> int:
    if obs.attrs.get("temporary"):
        return TEMPORARY_HALF_LIFE_DAYS
    return HALF_LIFE_DAYS[obs.type]


def is_community_verified(obs: Observation) -> bool:
    return obs.confirmations >= VERIFY_MIN_CONFIRMATIONS and obs.confirmations >= 2 * obs.denials


def reference_date(obs: Observation) -> date:
    """Data, od której liczymy wiek: obserwacja albo ostatnie potwierdzenie użytkownika."""
    if obs.last_confirmed_at is not None:
        return max(obs.source.observed_at, obs.last_confirmed_at)
    return obs.source.observed_at


def freshness(obs: Observation, today: date) -> float:
    age_days = max(0, (today - reference_date(obs)).days)
    return 0.5 ** (age_days / half_life_days(obs))


def vote_factor(obs: Observation) -> float:
    """1.0 bez głosów; zaprzeczenia obniżają wiarygodność, potwierdzenia ją odbudowują."""
    return (obs.confirmations + 1) / (obs.confirmations + obs.denials + 1)


def trust_score(obs: Observation, today: date) -> float:
    weight = SOURCE_WEIGHT[obs.source.type]
    if is_community_verified(obs):
        weight = max(weight, SOURCE_WEIGHT[SourceType.VERIFIED_USER])
    return round(weight * obs.confidence * vote_factor(obs) * freshness(obs, today), 4)


def data_status(obs: Observation, today: date) -> DataStatus:
    if freshness(obs, today) < 0.5:
        return DataStatus.OUTDATED
    if obs.source.type in CONFIRMED_SOURCES or is_community_verified(obs):
        return DataStatus.CONFIRMED
    return DataStatus.UNVERIFIED
