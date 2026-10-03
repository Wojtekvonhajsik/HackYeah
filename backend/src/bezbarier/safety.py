"""Analiza bezpieczeństwa miejsca - pokazywana jako pierwsza (najwyższy priorytet).

Łączy dwa poziomy:
- miasto: wskaźniki GUS (wypadki drogowe, ofiary śmiertelne, przestępczość) na tle średniej krajowej,
- okolica miejsca: przejścia dla pieszych z OpenStreetMap (sygnalizacja świetlna, dźwiękowa, ścieżka dotykowa),
oraz wskazówki dopasowane do profilu potrzeb. Dane GUS są dla całego miasta - mówimy o tym wprost.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .classification import FeatureType, Needs, Observation, group_observations
from .sources.gus import CitySafety

ELEVATED_RATIO = 1.5  # wskaźnik miasta co najmniej 1,5x wyższy niż średnia krajowa = podwyższone ryzyko
LOWER_RATIO = 0.8


class CrossingInfo(BaseModel):
    total: int
    with_signals: int
    with_sound: int
    with_tactile: int


class PlaceSafety(BaseModel):
    level: Literal["elevated", "typical", "lower", "unknown"]
    headline: str
    facts: list[str]
    tips: list[str]
    crossings: CrossingInfo
    city: CitySafety | None
    scope_note: str


def _num(value: float) -> str:
    return f"{value:.1f}".replace(".", ",").replace(",0", "") if value < 10 else f"{value:.0f}"


def crossing_info(observations: list[Observation]) -> CrossingInfo:
    """Przejścia w okolicy - kilka obserwacji tego samego przejścia liczymy raz (fuzja po odległości)."""
    crossings = [f for f in group_observations([o for o in observations if o.type == FeatureType.CROSSING])]

    def any_true(feature, key: str) -> bool:
        return any(o.attrs.get(key) is True for o in feature.observations)

    return CrossingInfo(
        total=len(crossings),
        with_signals=sum(any_true(f, "traffic_signals") for f in crossings),
        with_sound=sum(any_true(f, "sound_signals") for f in crossings),
        with_tactile=sum(any_true(f, "tactile_paving") for f in crossings),
    )


def assess_safety(city: CitySafety | None, observations: list[Observation], needs: Needs) -> PlaceSafety:
    crossings = crossing_info(observations)
    accidents = next((i for i in city.indicators if i.key == "accidents_per_100k"), None) if city else None

    if accidents is None or accidents.ratio is None:
        level, headline = "unknown", "Brak danych o bezpieczeństwie ruchu w mieście."
    elif accidents.ratio >= ELEVATED_RATIO:
        level = "elevated"
        headline = (
            f"Wypadków drogowych jest tu {_num(accidents.ratio)} raza więcej (na mieszkańca) niż średnio "
            f"w Polsce - uważaj na przejściach."
        )
    elif accidents.ratio <= LOWER_RATIO:
        level, headline = "lower", "Wypadków drogowych jest tu mniej niż średnio w Polsce."
    else:
        level, headline = "typical", "Liczba wypadków drogowych jest zbliżona do średniej w Polsce."

    facts = []
    for indicator in city.indicators if city else []:
        national = f" (Polska: {_num(indicator.country)})" if indicator.country is not None else ""
        label = indicator.label[:1].upper() + indicator.label[1:]  # capitalize() zmieniłoby "Policję" na "policję"
        facts.append(f"{label}: {_num(indicator.city)} {indicator.unit}{national}, {indicator.year}")
    if crossings.total:
        facts.append(
            f"Przejścia dla pieszych w pobliżu: {crossings.total}, z sygnalizacją świetlną: {crossings.with_signals}, "
            f"z dźwiękową: {crossings.with_sound}"
        )
    else:
        facts.append("Brak danych o przejściach dla pieszych w pobliżu")

    tips = []
    if needs.vision is not None:
        if crossings.total and crossings.with_sound < crossings.total:
            tips.append("Nie każde przejście w pobliżu ma sygnalizację dźwiękową - wybierz takie z dźwiękiem albo przejdź z asystą.")
        elif not crossings.total:
            tips.append("Nie wiemy, czy przejścia w pobliżu mają sygnalizację dźwiękową - zaplanuj trasę z asystą.")
    if needs.mobility is not None and level == "elevated":
        tips.append("Przechodź na przejściach z sygnalizacją i obniżonym krawężnikiem - kierowcy mają wtedy więcej czasu, by Cię zauważyć.")
    if level == "elevated" and crossings.total and crossings.with_signals < crossings.total and not tips:
        tips.append("Wybieraj przejścia z sygnalizacją świetlną.")

    source = "statystyki GUS dla całego miasta" if city else "brak danych GUS"
    return PlaceSafety(
        level=level,
        headline=headline,
        facts=facts,
        tips=tips[:2],
        crossings=crossings,
        city=city,
        scope_note=f"Źródło: {source}; przejścia - OpenStreetMap, w promieniu ok. 30 m.",
    )
