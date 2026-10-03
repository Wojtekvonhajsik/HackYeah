from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Verdict(str, Enum):
    BLOCKER = "blocker"      # dla Twoich ustawień: nie do pokonania
    UNCERTAIN = "uncertain"  # zakres pomiaru przecina Twój limit - może być blokadą, do sprawdzenia
    DIFFICULT = "difficult"  # da się, ale z trudem / z pomocą
    UNKNOWN = "unknown"      # brak danych - NIGDY nie traktujemy jak OK
    OK = "ok"
    AMENITY = "amenity"      # udogodnienie


SEVERITY: dict[Verdict, int] = {
    Verdict.AMENITY: 0,
    Verdict.OK: 1,
    Verdict.UNKNOWN: 2,
    Verdict.DIFFICULT: 3,
    Verdict.UNCERTAIN: 4,
    Verdict.BLOCKER: 5,
}

# Do wykrywania sprzeczności: różne werdykty w tej samej grupie to nie konflikt.
AGREEMENT_GROUP: dict[Verdict, str] = {
    Verdict.AMENITY: "good",
    Verdict.OK: "good",
    Verdict.DIFFICULT: "hard",
    Verdict.UNCERTAIN: "hard",
    Verdict.BLOCKER: "blocked",
    Verdict.UNKNOWN: "unknown",
}


class RuleResult(BaseModel):
    verdict: Verdict
    reasons: list[str]


def combine(*results: RuleResult | None) -> RuleResult | None:
    """Najgorszy werdykt wygrywa; uzasadnienia sklejane od najgorszego. None = nieistotne dla profilu."""
    present = [r for r in results if r is not None]
    if not present:
        return None
    present.sort(key=lambda r: SEVERITY[r.verdict], reverse=True)
    return RuleResult(
        verdict=present[0].verdict,
        reasons=[reason for r in present for reason in r.reasons],
    )
