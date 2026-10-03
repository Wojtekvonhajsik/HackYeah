"""Klasyfikator: cechy x potrzeby -> ocena per bariera + podsumowanie miejsca/odcinka.

Czysta funkcja bez I/O - da się ją uruchomić po stronie klienta, wtedy profil
użytkownika w ogóle nie opuszcza urządzenia.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .models import Feature, FeatureType, GeoPoint, Source
from .needs import Needs
from .rules import evaluate
from .trust import DataStatus, data_status, trust_score
from .verdict import AGREEMENT_GROUP, SEVERITY, Verdict

# Obserwacja jest "porównywalnie wiarygodna" z najlepszą, jeśli ma co najmniej tyle jej trust.
CONFLICT_TRUST_RATIO = 0.5

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


class FeatureAssessment(BaseModel):
    feature_id: str
    type: FeatureType
    label: str
    location: GeoPoint
    verdict: Verdict
    reasons: list[str]
    status: DataStatus
    trust: float
    conflict: bool
    evidence: list[Evidence]  # wszystkie źródła, najbardziej wiarygodne pierwsze


class Summary(str, Enum):
    BARRIERS = "barriers"
    DIFFICULTIES = "difficulties"
    INCOMPLETE_DATA = "incomplete_data"
    NO_KNOWN_BARRIERS = "no_known_barriers"  # celowo NIE "accessible"


class Assessment(BaseModel):
    summary: Summary
    summary_text: str
    features: list[FeatureAssessment]  # od najpoważniejszych
    missing: list[str]                 # kluczowe informacje, których nie mamy
    counts: dict[Verdict, int]
    contains_sample_data: bool
    contains_unverified: bool
    warnings: list[str] = Field(default_factory=list)  # np. niedostępne źródło danych


def default_required(needs: Needs, kind: Literal["place", "route"]) -> set[FeatureType]:
    """Bez tych informacji nie mówimy 'brak znanych barier', tylko 'niepełne dane'."""
    required: set[FeatureType] = set()
    if needs.mobility is not None:
        required |= {FeatureType.ENTRANCE} if kind == "place" else {FeatureType.SURFACE, FeatureType.PATH_WIDTH}
    if needs.vision is not None and kind == "route":
        required |= {FeatureType.CROSSING}
    return required


def assess_feature(feature: Feature, needs: Needs, today: date) -> FeatureAssessment | None:
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

    return FeatureAssessment(
        feature_id=feature.id,
        type=feature.type,
        label=FEATURE_PL[feature.type],
        location=feature.location,
        verdict=chosen.verdict,
        reasons=chosen.reasons,
        status=status,
        trust=chosen.trust,
        conflict=conflict,
        evidence=evidence,
    )


def assess(
    features: Iterable[Feature],
    needs: Needs,
    today: date | None = None,
    required: Iterable[FeatureType] = (),
    warnings: Iterable[str] = (),
) -> Assessment:
    """warnings: problemy ze źródłami (np. niedostępne OSM) - wtedy nie mówimy 'brak znanych barier'."""
    warnings = list(warnings)
    today = today or date.today()
    features = list(features)
    assessed = [a for f in features if (a := assess_feature(f, needs, today)) is not None]
    assessed.sort(key=lambda a: (SEVERITY[a.verdict], -a.trust), reverse=True)

    counts = {v: 0 for v in Verdict}
    for a in assessed:
        counts[a.verdict] += 1

    missing = [
        FEATURE_PL[t]
        for t in sorted(set(required), key=lambda t: t.value)
        if not any(a.type == t for a in assessed)  # jeśli cecha jest, ale niepełna - niżej konkretne braki
    ]
    missing += [
        r.removeprefix("brak danych: ")
        for a in assessed
        if a.verdict == Verdict.UNKNOWN
        for r in a.reasons
        if r.startswith("brak danych")
    ]
    missing = list(dict.fromkeys(missing))  # bez duplikatów, z zachowaniem kolejności

    if counts[Verdict.BLOCKER]:
        summary = Summary.BARRIERS
        text = f"Prawdopodobnie nie do pokonania przy Twoich ustawieniach: {counts[Verdict.BLOCKER]} blokad(y)."
    elif counts[Verdict.UNCERTAIN] or counts[Verdict.DIFFICULT]:
        summary = Summary.DIFFICULTIES
        text = (
            f"Utrudnienia: {counts[Verdict.DIFFICULT]}, "
            f"do sprawdzenia (mogą być blokadą): {counts[Verdict.UNCERTAIN]}."
        )
    elif not assessed:
        summary = Summary.INCOMPLETE_DATA
        text = "Brak danych o tym miejscu istotnych dla Twoich ustawień - nie możemy ocenić dostępności."
    elif missing or warnings:
        summary = Summary.INCOMPLETE_DATA
        text = "Niepełne dane - nie możemy ocenić dostępności."
    else:
        summary = Summary.NO_KNOWN_BARRIERS
        text = "Nie znaleźliśmy barier w dostępnych danych. To nie jest gwarancja dostępności."
    if missing and summary != Summary.INCOMPLETE_DATA:
        text += " Brakuje danych: " + ", ".join(missing) + "."

    all_evidence = [e for a in assessed for e in a.evidence]
    return Assessment(
        summary=summary,
        summary_text=text,
        features=assessed,
        missing=missing,
        counts=counts,
        contains_sample_data=any(e.source.sample for e in all_evidence),
        contains_unverified=any(e.status != DataStatus.CONFIRMED for e in all_evidence),
        warnings=warnings,
    )
