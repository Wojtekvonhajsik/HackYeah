"""Klasyfikator: cechy x potrzeby -> ocena per bariera + podsumowanie miejsca/odcinka.

Czysta funkcja bez I/O - da się ją uruchomić po stronie klienta, wtedy profil
użytkownika w ogóle nie opuszcza urządzenia.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from enum import Enum
from math import prod
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from .describe import describe
from .models import Feature, FeatureType, GeoPoint, Source
from .needs import Needs
from .rules import evaluate
from .trust import DataStatus, data_status, trust_score
from .verdict import AGREEMENT_GROUP, SEVERITY, Verdict

# Obserwacja jest "porównywalnie wiarygodna" z najlepszą, jeśli ma co najmniej tyle jej trust.
CONFLICT_TRUST_RATIO = 0.5

# Przeszkoda z niższą pewnością jest pokazywana jako "do sprawdzenia" - np. stare zdjęcie sprzed lat
# nie powinno samo przesądzać, że miejsce jest "nie do pokonania".
MIN_BLOCKER_CONFIDENCE_PCT = 30

FEATURE_PL: dict[FeatureType, str] = {
    FeatureType.KERB: "krawężnik",
    FeatureType.STEPS: "schody",
    FeatureType.RAMP: "podjazd",
    FeatureType.ENTRANCE: "wejście",
    FeatureType.SURFACE: "nawierzchnia",
    FeatureType.PATH_WIDTH: "szerokość przejścia",
    FeatureType.INCLINE: "nachylenie",
    FeatureType.OBSTACLE: "przeszkoda",
    FeatureType.CROSSING: "przejście dla pieszych",
    FeatureType.AMENITY: "udogodnienie",
}


class Evidence(BaseModel):
    """Ocena jednej obserwacji - pokazywana jako 'skąd to wiemy'."""

    observation_id: str
    source: Source
    verdict: Verdict
    reasons: list[str]
    trust: float
    status: DataStatus
    confirmations: int
    denials: int
    last_confirmed_at: date | None
    note: str | None = None  # np. opis z detektora albo "oszacowane z tagu OSM wheelchair=yes"


class Scope(str, Enum):
    PLACE = "place"                # samo miejsce i jego wejście - decyduje o nagłówku oceny
    SURROUNDINGS = "surroundings"  # okolica (chodnik, schody obok) - pokazywana osobno, nie przesądza o ocenie


class FeatureAssessment(BaseModel):
    feature_id: str
    type: FeatureType
    label: str
    location: GeoPoint
    scope: Scope
    verdict: Verdict
    reasons: list[str]
    status: DataStatus
    trust: float
    # Na ile dane potwierdzają werdykt (0-100). Szacunek z wiarygodności zgodnych i sprzecznych źródeł,
    # nie skalibrowane prawdopodobieństwo. None przy braku danych.
    confidence_pct: int | None
    conflict: bool
    primary_observation_id: str  # obserwacja, z której pochodzi werdykt (przy konflikcie - ostrożniejsza)
    evidence: list[Evidence]  # wszystkie źródła, najbardziej wiarygodne pierwsze


class Summary(str, Enum):
    BARRIERS = "barriers"
    DIFFICULTIES = "difficulties"
    INCOMPLETE_DATA = "incomplete_data"
    NO_KNOWN_BARRIERS = "no_known_barriers"  # celowo NIE "accessible"


class Assessment(BaseModel):
    summary: Summary                    # ocena SAMEGO miejsca (scope=place)
    summary_text: str
    summary_confidence_pct: int | None  # pewność nagłówka; None przy niepełnych danych
    features: list[FeatureAssessment]   # najpierw miejsce, potem okolica; w grupach od najpoważniejszych
    missing: list[str]                  # kluczowe informacje o miejscu, których nie mamy
    counts: dict[Verdict, int]               # cechy miejsca
    surroundings_counts: dict[Verdict, int]  # cechy okolicy
    contains_sample_data: bool
    contains_unverified: bool
    warnings: list[str] = Field(default_factory=list)  # np. niedostępne źródło danych

    @computed_field  # liczone przy serializacji, więc obejmuje też ostrzeżenia dopisane po assess()
    @property
    def text(self) -> str:
        """Cała ocena jako tekst - dla czytnika ekranu, asystenta głosowego i jako alternatywa dla mapy."""
        return describe(self)


def default_required(needs: Needs, kind: Literal["place", "route"]) -> set[FeatureType]:
    """Bez tych informacji nie mówimy 'brak znanych barier', tylko 'niepełne dane'."""
    required: set[FeatureType] = set()
    if needs.mobility is not None:
        required |= {FeatureType.ENTRANCE} if kind == "place" else {FeatureType.SURFACE, FeatureType.PATH_WIDTH}
    if needs.vision is not None and kind == "route":
        required |= {FeatureType.CROSSING}
    return required


def confidence_pct(chosen: Evidence, evidence: list[Evidence]) -> int | None:
    """Zgodne źródła wzmacniają się (1 - iloczyn szans, że każde się myli), sprzeczne osłabiają."""
    if chosen.verdict == Verdict.UNKNOWN:
        return None
    group = AGREEMENT_GROUP[chosen.verdict]
    known = [e for e in evidence if e.verdict != Verdict.UNKNOWN]
    support = 1 - prod(1 - e.trust for e in known if AGREEMENT_GROUP[e.verdict] == group)
    against = 1 - prod(1 - e.trust for e in known if AGREEMENT_GROUP[e.verdict] != group)
    confidence = support * (1 - against)
    if chosen.verdict == Verdict.UNCERTAIN:
        confidence = min(confidence, 0.5)  # "do sprawdzenia" z definicji nie jest pewne
    return round(confidence * 100)


def assess_feature(
    feature: Feature, needs: Needs, today: date, place_id: str | None = None
) -> FeatureAssessment | None:
    evidence: list[Evidence] = []
    for obs in feature.observations:
        result = evaluate(obs.type, obs.attrs, needs)
        if result is None:
            continue
        evidence.append(Evidence(
            observation_id=obs.id,
            source=obs.source,
            verdict=result.verdict,
            reasons=result.reasons,
            trust=trust_score(obs, today),
            status=data_status(obs, today),
            confirmations=obs.confirmations,
            denials=obs.denials,
            last_confirmed_at=obs.last_confirmed_at,
            note=obs.attrs.get("description"),
        ))
    if not evidence:
        return None  # cecha nieistotna dla tego profilu
    evidence.sort(key=lambda e: e.trust, reverse=True)

    known = [e for e in evidence if e.verdict != Verdict.UNKNOWN]
    conflict = False
    if not known:
        chosen, status = evidence[0], evidence[0].status
    else:
        best = known[0]
        rivals = [e for e in known if e.trust >= CONFLICT_TRUST_RATIO * best.trust]
        conflict = len({AGREEMENT_GROUP[e.verdict] for e in rivals}) > 1
        if conflict:
            # Przy sprzecznych źródłach pokazujemy ostrożniejszy wariant + wszystkie dowody.
            chosen = max(rivals, key=lambda e: SEVERITY[e.verdict])
            status = DataStatus.CONFLICTING
        else:
            chosen, status = best, best.status

    verdict, reasons = chosen.verdict, chosen.reasons
    confidence = confidence_pct(chosen, evidence)
    if verdict == Verdict.BLOCKER and confidence is not None and confidence < MIN_BLOCKER_CONFIDENCE_PCT:
        verdict = Verdict.UNCERTAIN
        reasons = [*reasons, f"słabe dane (pewność {confidence}%) - wymaga sprawdzenia"]

    # Bez wskazanego miejsca (np. /classify) wszystko traktujemy jak miejsce
    in_place = place_id is None or any(obs.place_id == place_id for obs in feature.observations)
    return FeatureAssessment(
        feature_id=feature.id,
        type=feature.type,
        label=FEATURE_PL[feature.type],
        location=feature.location,
        scope=Scope.PLACE if in_place else Scope.SURROUNDINGS,
        verdict=verdict,
        reasons=reasons,
        status=status,
        trust=chosen.trust,
        confidence_pct=confidence,
        conflict=conflict,
        primary_observation_id=chosen.observation_id,
        evidence=evidence,
    )


def _count(features: list[FeatureAssessment]) -> dict[Verdict, int]:
    counts = {v: 0 for v in Verdict}
    for a in features:
        counts[a.verdict] += 1
    return counts


def _surroundings_text(features: list[FeatureAssessment]) -> str:
    """Np. ' W okolicy: schody (przeszkoda) x7, winda. Sprawdź trasę dojścia.'"""
    notable = {Verdict.BLOCKER: "przeszkoda", Verdict.UNCERTAIN: "do sprawdzenia", Verdict.DIFFICULT: "utrudnienie"}
    groups: dict[str, int] = {}
    for a in features:
        if a.verdict in notable:
            key = f"{a.label} ({notable[a.verdict]})"
        elif a.verdict == Verdict.AMENITY:
            key = "; ".join(a.reasons)
        else:
            continue
        groups[key] = groups.get(key, 0) + 1
    if not groups:
        return ""
    parts = [key if n == 1 else f"{key} x{n}" for key, n in groups.items()]
    return " W okolicy: " + ", ".join(parts) + ". Sprawdź trasę dojścia."


def assess(
    features: Iterable[Feature],
    needs: Needs,
    today: date | None = None,
    required: Iterable[FeatureType] = (),
    warnings: Iterable[str] = (),
    place_id: str | None = None,
) -> Assessment:
    """warnings: problemy ze źródłami (np. niedostępne OSM) - wtedy nie mówimy 'brak znanych barier'.

    place_id: cechy przypisane do tego miejsca (wejście, tagi obiektu) decydują o nagłówku; reszta to okolica.
    """
    warnings = list(warnings)
    today = today or date.today()
    assessed = [a for f in features if (a := assess_feature(f, needs, today, place_id)) is not None]
    assessed.sort(key=lambda a: (a.scope == Scope.PLACE, SEVERITY[a.verdict], -a.trust), reverse=True)
    place = [a for a in assessed if a.scope == Scope.PLACE]
    around = [a for a in assessed if a.scope == Scope.SURROUNDINGS]
    counts = _count(place)

    missing = [
        FEATURE_PL[t]
        for t in sorted(set(required), key=lambda t: t.value)
        if not any(a.type == t for a in place)  # jeśli cecha jest, ale niepełna - niżej konkretne braki
    ]
    missing += [
        r.removeprefix("brak danych: ")
        for a in place
        if a.verdict == Verdict.UNKNOWN
        for r in a.reasons
        if r.startswith("brak danych")
    ]
    missing = list(dict.fromkeys(missing))  # bez duplikatów, z zachowaniem kolejności

    def most_confident(verdicts: set[Verdict]) -> int | None:
        values = [a.confidence_pct for a in place if a.verdict in verdicts and a.confidence_pct is not None]
        return max(values, default=None)

    if counts[Verdict.BLOCKER]:
        summary = Summary.BARRIERS
        text = f"Prawdopodobnie nie do pokonania przy Twoich ustawieniach: {counts[Verdict.BLOCKER]} blokad(y)."
        summary_confidence = most_confident({Verdict.BLOCKER})
    elif counts[Verdict.UNCERTAIN] or counts[Verdict.DIFFICULT]:
        summary = Summary.DIFFICULTIES
        text = (
            f"Utrudnienia: {counts[Verdict.DIFFICULT]}, "
            f"do sprawdzenia (mogą być blokadą): {counts[Verdict.UNCERTAIN]}."
        )
        summary_confidence = most_confident({Verdict.DIFFICULT, Verdict.UNCERTAIN})
    elif not place:
        summary = Summary.INCOMPLETE_DATA
        text = "Brak danych o samym miejscu istotnych dla Twoich ustawień - nie możemy ocenić dostępności."
        summary_confidence = None
    elif missing or warnings:
        summary = Summary.INCOMPLETE_DATA
        text = "Niepełne dane - nie możemy ocenić dostępności."
        summary_confidence = None
    else:
        summary = Summary.NO_KNOWN_BARRIERS
        text = "Nie znaleźliśmy barier w dostępnych danych. To nie jest gwarancja dostępności."
        # "brak barier" jest tak pewny, jak najsłabiej potwierdzona informacja o miejscu
        summary_confidence = min((a.confidence_pct for a in place if a.confidence_pct is not None), default=None)
    if missing and summary != Summary.INCOMPLETE_DATA:
        text += " Brakuje danych: " + ", ".join(missing) + "."
    text += _surroundings_text(around)

    all_evidence = [e for a in assessed for e in a.evidence]
    return Assessment(
        summary=summary,
        summary_text=text,
        summary_confidence_pct=summary_confidence,
        features=assessed,
        missing=missing,
        counts=counts,
        surroundings_counts=_count(around),
        contains_sample_data=any(e.source.sample for e in all_evidence),
        contains_unverified=any(e.status != DataStatus.CONFIRMED for e in all_evidence),
        warnings=warnings,
    )
