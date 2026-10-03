"""Reguły: (atrybuty cechy, potrzeby użytkownika) -> werdykt + uzasadnienie po polsku.

Każda reguła zwraca None, gdy cecha nie ma znaczenia dla danego profilu
(np. nawierzchnia dla osoby niewidomej) - wtedy cecha nie jest pokazywana.
Żeby dodać nowy typ cechy: dopisz FeatureType w models.py i funkcję w RULES.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .models import FeatureType, Range
from .needs import MaxThreshold, MinThreshold, MobilityNeeds, Needs
from .verdict import RuleResult, Verdict, combine

Attrs = dict[str, Any]
Rule = Callable[[Attrs, Needs], RuleResult | None]

SURFACE_PL = {
    "asphalt": "asfalt",
    "concrete": "beton",
    "paving_stones": "kostka/płyty chodnikowe",
    "sett": "kostka kamienna",
    "cobblestone": "bruk",
    "unhewn_cobblestone": "bruk z nieobrobionych kamieni",
    "gravel": "żwir",
    "fine_gravel": "drobny żwir",
    "compacted": "utwardzona ziemia",
    "grass": "trawa",
    "sand": "piasek",
    "dirt": "ziemia",
    "ground": "grunt",
    "mud": "błoto",
    "wood": "drewno",
}

AMENITY_PL = {
    "bench": "ławka",
    "toilets_wheelchair": "toaleta dostępna dla wózków",
    "elevator": "winda",
}


def check_max(value: Range | None, t: MaxThreshold, label: str, unit: str = "") -> RuleResult:
    if value is None:
        return RuleResult(verdict=Verdict.UNKNOWN, reasons=[f"brak danych: {label}"])
    shown = f"{label} {value}{unit}"
    if value.lo > t.hard:
        return RuleResult(verdict=Verdict.BLOCKER, reasons=[f"{shown} - powyżej Twojego limitu {t.hard:g}{unit}"])
    if value.hi <= t.soft:
        return RuleResult(verdict=Verdict.OK, reasons=[f"{shown} - w normie"])
    if value.hi <= t.hard:
        return RuleResult(
            verdict=Verdict.DIFFICULT,
            reasons=[f"{shown} - powyżej komfortowego {t.soft:g}{unit}, w granicy {t.hard:g}{unit}"],
        )
    return RuleResult(
        verdict=Verdict.UNCERTAIN,
        reasons=[f"{shown} - może przekraczać Twój limit {t.hard:g}{unit}, wymaga sprawdzenia"],
    )


def check_min(value: Range | None, t: MinThreshold, label: str, unit: str = "") -> RuleResult:
    if value is None:
        return RuleResult(verdict=Verdict.UNKNOWN, reasons=[f"brak danych: {label}"])
    shown = f"{label} {value}{unit}"
    if value.hi < t.hard:
        return RuleResult(verdict=Verdict.BLOCKER, reasons=[f"{shown} - poniżej Twojego minimum {t.hard:g}{unit}"])
    if value.lo >= t.soft:
        return RuleResult(verdict=Verdict.OK, reasons=[f"{shown} - w normie"])
    if value.lo >= t.hard:
        return RuleResult(
            verdict=Verdict.DIFFICULT,
            reasons=[f"{shown} - poniżej komfortowych {t.soft:g}{unit}, powyżej minimum {t.hard:g}{unit}"],
        )
    return RuleResult(
        verdict=Verdict.UNCERTAIN,
        reasons=[f"{shown} - może być węższe niż Twoje minimum {t.hard:g}{unit}, wymaga sprawdzenia"],
    )


def _flag(value: bool | None, ok: str, bad: str, unknown: str, bad_verdict: Verdict = Verdict.DIFFICULT) -> RuleResult:
    if value is True:
        return RuleResult(verdict=Verdict.OK, reasons=[ok])
    if value is False:
        return RuleResult(verdict=bad_verdict, reasons=[bad])
    return RuleResult(verdict=Verdict.UNKNOWN, reasons=[unknown])


def _edge_check(kind: str | None, height_raw: Any, m: MobilityNeeds, label: str) -> RuleResult:
    height = Range.parse(height_raw)
    if height is None:
        if kind in ("lowered", "flush"):
            return RuleResult(verdict=Verdict.OK, reasons=[f"{label} obniżony"])
        if kind == "raised":
            # OSM kerb=raised bez wysokości: typowo >3 cm, nie wiemy ile dokładnie
            height = Range(lo=3, hi=20)
    return check_max(height, m.max_edge_height_cm, label, " cm")


def rule_kerb(a: Attrs, n: Needs) -> RuleResult | None:
    if n.mobility is None:
        return None
    return _edge_check(a.get("kind"), a.get("height_cm"), n.mobility, "krawężnik")


def rule_steps(a: Attrs, n: Needs) -> RuleResult | None:
    results: list[RuleResult | None] = []
    if (m := n.mobility) is not None:
        if a.get("ramp") or a.get("elevator"):
            alt = "podjazd" if a.get("ramp") else "winda"
            results.append(RuleResult(verdict=Verdict.OK, reasons=[f"schody, ale jest {alt}"]))
        else:
            # brak liczby schodów = co najmniej jeden stopień
            count = Range.parse(a.get("count")) or Range(lo=1, hi=math.inf)
            results.append(check_max(count, m.max_step_count, "liczba schodów"))
            if m.needs_handrail and a.get("handrail") is False:
                results.append(RuleResult(verdict=Verdict.DIFFICULT, reasons=["schody bez poręczy"]))
    if (v := n.vision) is not None:
        if v.needs_step_contrast:
            results.append(_flag(
                a.get("contrast_marking"),
                ok="krawędzie schodów oznaczone kontrastowo",
                bad="krawędzie schodów bez kontrastowego oznaczenia",
                unknown="brak danych o oznaczeniu krawędzi schodów",
            ))
        if a.get("handrail") is False:
            results.append(RuleResult(verdict=Verdict.DIFFICULT, reasons=["schody bez poręczy"]))
    return combine(*results)


def rule_ramp(a: Attrs, n: Needs) -> RuleResult | None:
    if (m := n.mobility) is None:
        return None
    result = combine(
        check_max(Range.parse(a.get("incline_pct")), m.max_incline_pct, "nachylenie podjazdu", "%"),
        check_min(Range.parse(a.get("width_cm")), m.min_width_cm, "szerokość podjazdu", " cm"),
    )
    assert result is not None
    if result.verdict == Verdict.OK:
        return RuleResult(verdict=Verdict.AMENITY, reasons=["podjazd", *result.reasons])
    return result


def rule_entrance(a: Attrs, n: Needs) -> RuleResult | None:
    if (m := n.mobility) is None:
        return None
    result = combine(
        check_min(Range.parse(a.get("width_cm")), m.min_width_cm, "szerokość wejścia", " cm"),
        check_max(Range.parse(a.get("threshold_cm")), m.max_edge_height_cm, "próg w wejściu", " cm"),
    )
    assert result is not None
    if a.get("automatic_door"):
        result.reasons.append("drzwi automatyczne")
    return result


def rule_surface(a: Attrs, n: Needs) -> RuleResult | None:
    if (m := n.mobility) is None:
        return None
    value = a.get("value")
    if value is None:
        return RuleResult(verdict=Verdict.UNKNOWN, reasons=["brak danych: nawierzchnia"])
    shown = f"nawierzchnia: {SURFACE_PL.get(value, value)}"
    if value in m.blocking_surfaces:
        return RuleResult(verdict=Verdict.BLOCKER, reasons=[f"{shown} - nieprzejezdna dla Twoich ustawień"])
    if value in m.difficult_surfaces:
        return RuleResult(verdict=Verdict.DIFFICULT, reasons=[f"{shown} - utrudnia poruszanie się"])
    return RuleResult(verdict=Verdict.OK, reasons=[shown])


def rule_path_width(a: Attrs, n: Needs) -> RuleResult | None:
    if (m := n.mobility) is None:
        return None
    return check_min(Range.parse(a.get("width_cm")), m.min_width_cm, "szerokość przejścia", " cm")


def rule_incline(a: Attrs, n: Needs) -> RuleResult | None:
    if (m := n.mobility) is None:
        return None
    return check_max(Range.parse(a.get("incline_pct")), m.max_incline_pct, "nachylenie", "%")


def rule_obstacle(a: Attrs, n: Needs) -> RuleResult | None:
    kind = a.get("kind") or "przeszkoda"
    results: list[RuleResult | None] = []
    if (m := n.mobility) is not None:
        remaining = Range.parse(a.get("remaining_width_cm"))
        blocks = a.get("blocks_path")
        if remaining is not None:
            results.append(check_min(remaining, m.min_width_cm, f"wolne przejście obok ({kind})", " cm"))
        elif blocks is True:
            results.append(RuleResult(verdict=Verdict.BLOCKER, reasons=[f"{kind} blokuje przejście"]))
        elif blocks is False:
            results.append(RuleResult(verdict=Verdict.OK, reasons=[f"{kind} - da się ominąć"]))
        else:
            results.append(RuleResult(verdict=Verdict.UNCERTAIN, reasons=[f"{kind} - nie wiadomo, czy da się ominąć"]))
    if (v := n.vision) is not None:
        bottom = Range.parse(a.get("height_from_ground_cm"))  # wysokość dolnej krawędzi przeszkody
        if bottom is not None and bottom.lo < v.overhang_max_cm and bottom.hi > v.overhang_min_cm:
            results.append(RuleResult(
                verdict=Verdict.BLOCKER,
                reasons=[f"{kind} zawieszona na wysokości {bottom} cm - niewykrywalna białą laską"],
            ))
        else:
            results.append(RuleResult(verdict=Verdict.DIFFICULT, reasons=[f"{kind} na drodze"]))
    result = combine(*results)
    if result is not None and a.get("temporary"):
        result.reasons = [f"{r} (tymczasowe)" for r in result.reasons]
    return result


def rule_crossing(a: Attrs, n: Needs) -> RuleResult | None:
    results: list[RuleResult | None] = []
    if (m := n.mobility) is not None:
        results.append(_edge_check(a.get("kerb"), a.get("kerb_height_cm"), m, "krawężnik na przejściu"))
    if (v := n.vision) is not None:
        if v.needs_tactile_paving:
            results.append(_flag(
                a.get("tactile_paving"),
                ok="jest ścieżka dotykowa",
                bad="brak ścieżki dotykowej",
                unknown="brak danych o ścieżce dotykowej",
            ))
        if v.needs_sound_signals and a.get("traffic_signals"):
            results.append(_flag(
                a.get("sound_signals"),
                ok="sygnalizacja dźwiękowa",
                bad="sygnalizacja świetlna bez dźwięku",
                unknown="brak danych o sygnalizacji dźwiękowej",
            ))
    return combine(*results)


def rule_amenity(a: Attrs, n: Needs) -> RuleResult | None:
    kind = a.get("kind")
    if kind in n.wants or (kind == "elevator" and n.mobility is not None):
        return RuleResult(verdict=Verdict.AMENITY, reasons=[AMENITY_PL.get(kind, kind)])
    return None


RULES: dict[FeatureType, Rule] = {
    FeatureType.KERB: rule_kerb,
    FeatureType.STEPS: rule_steps,
    FeatureType.RAMP: rule_ramp,
    FeatureType.ENTRANCE: rule_entrance,
    FeatureType.SURFACE: rule_surface,
    FeatureType.PATH_WIDTH: rule_path_width,
    FeatureType.INCLINE: rule_incline,
    FeatureType.OBSTACLE: rule_obstacle,
    FeatureType.CROSSING: rule_crossing,
    FeatureType.AMENITY: rule_amenity,
}


def evaluate(feature_type: FeatureType, attrs: Attrs, needs: Needs) -> RuleResult | None:
    return RULES[feature_type](attrs, needs)
