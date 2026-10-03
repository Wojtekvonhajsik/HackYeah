"""Ocena jako zwykły tekst po polsku - dla czytnika ekranu, asystenta głosowego i jako tekstowa
alternatywa dla informacji pokazywanych na mapie (wymóg WCAG z kryteriów wyzwania).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from .models import FeatureType, SourceType
from .trust import DataStatus
from .verdict import Verdict

if TYPE_CHECKING:
    from .classifier import Assessment, FeatureAssessment

VERDICT_PL: dict[Verdict, str] = {
    Verdict.BLOCKER: "przeszkoda nie do pokonania przy Twoich ustawieniach",
    Verdict.UNCERTAIN: "może być przeszkodą, wymaga sprawdzenia",
    Verdict.DIFFICULT: "utrudnienie",
    Verdict.UNKNOWN: "brak danych",
    Verdict.OK: "bez przeszkód",
    Verdict.AMENITY: "udogodnienie",
}

STATUS_PL: dict[DataStatus, str] = {
    DataStatus.CONFIRMED: "potwierdzone",
    DataStatus.UNVERIFIED: "niepotwierdzone",
    DataStatus.OUTDATED: "nieaktualne",
    DataStatus.CONFLICTING: "źródła się nie zgadzają",
}

SOURCE_PL: dict[SourceType, str] = {
    SourceType.OFFICIAL: "dane urzędowe",
    SourceType.OWNER: "właściciel obiektu",
    SourceType.VERIFIED_USER: "zweryfikowane zgłoszenia",
    SourceType.OSM: "OpenStreetMap",
    SourceType.USER_REPORT: "zgłoszenie użytkownika",
    SourceType.AI_DETECTION: "automatyczna analiza zdjęcia",
}


def _date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]  # str.capitalize() zmieniłoby resztę na małe litery (np. "OSM")


def describe_feature(f: FeatureAssessment) -> str:
    source = next(e for e in f.evidence if e.observation_id == f.primary_observation_id)
    origin = f"{SOURCE_PL[source.source.type]} z {_date(source.source.observed_at)}"
    if source.source.sample:
        origin += ", dane przykładowe"
    if f.type == FeatureType.AMENITY:
        text = f"Udogodnienie: {'; '.join(f.reasons)}."
    else:
        text = f"{_cap(f.label)}: {VERDICT_PL[f.verdict]}. {_cap('; '.join(f.reasons))}."
    if f.confidence_pct is not None and f.type != FeatureType.AMENITY:
        text += f" Pewność: {f.confidence_pct}%."
    text += f" Źródło: {origin} ({STATUS_PL[f.status]})."
    if f.conflict:
        text += f" Uwaga: {len(f.evidence)} źródła podają różne informacje."
    elif len(f.evidence) > 1:
        text += f" Liczba źródeł: {len(f.evidence)}."
    return text


def describe(a: Assessment) -> str:
    from .classifier import Scope  # import tutaj - classifier importuje ten moduł

    header = a.summary_text
    if a.summary_confidence_pct is not None:
        header += f" Pewność oceny: {a.summary_confidence_pct}%."
    lines = [header]
    place = [f for f in a.features if f.scope == Scope.PLACE]
    around = [f for f in a.features if f.scope == Scope.SURROUNDINGS]
    if place:
        lines.append("Miejsce i wejście:")
        lines += [describe_feature(f) for f in place]
    if around:
        lines.append("W okolicy (mogą istnieć inne drogi dojścia):")
        lines += [describe_feature(f) for f in around]
    lines += [f"Ostrzeżenie: {w}" for w in a.warnings]
    if a.contains_sample_data:
        lines.append("Uwaga: ocena zawiera dane przykładowe, nie opisują rzeczywistego obiektu.")
    return "\n".join(lines)
