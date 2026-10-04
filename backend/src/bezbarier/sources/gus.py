"""Źródło danych: GUS - Bank Danych Lokalnych (https://bdl.stat.gov.pl, licencja CC BY 4.0).

Dane GUS są zagregowane dla gminy (nie dla pojedynczych obiektów), więc nie wpływają na ocenę miejsca.
Pokazują skalę potrzeb w mieście i stan dostępności bazy noclegowej - kontekst dla użytkownika
i argument dla właścicieli obiektów. Inne miasto = inny GUS_UNIT_ID (identyfikator jednostki BDL).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

BDL_URL = "https://bdl.stat.gov.pl/api/v1"
KRAKOW_UNIT_ID = "011212161011"
USER_AGENT = "Dostepni.pl/0.1 (HackYeah prototype)"
LICENSE = "CC BY 4.0"
ATTRIBUTION = "Główny Urząd Statystyczny, Bank Danych Lokalnych"
CACHE_TTL_S = 12 * 3600

# Zmienne BDL (id -> znaczenie). Opis zmiennej: https://bdl.stat.gov.pl/bdl/metadane/cechy/<id>
VARIABLES = {
    "population": "34038",              # ludność ogółem (P1342)
    "post_working_age": "155",          # ludność w wieku poprodukcyjnym (P1342)
    "disabled": "1701618",              # osoby niepełnosprawne razem, NSP 2021 (P4322)
    "disabled_legal": "1701621",        # osoby niepełnosprawne prawnie (z orzeczeniem), NSP 2021 (P4322)
    "lodging_total": "289548",          # turystyczne obiekty noclegowe ogółem (P3186)
    "lodging_elevator": "500424",       # obiekty z windą przystosowaną dla os. niepełnosprawnych ruchowo (P3610)
    "lodging_ramp": "500428",           # obiekty z pochylnią wjazdową (P3610)
    "lodging_auto_door": "500427",      # obiekty z drzwiami automatycznie otwieranymi (P3610)
    "lodging_parking": "500422",        # obiekty z parkingiem z miejscami dla os. niepełnosprawnych (P3610)
}

SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "data"


class StatItem(BaseModel):
    key: str
    label: str
    value: int
    unit: str
    year: int
    share_pct: float | None = None  # udział w całości (np. % mieszkańców, % obiektów noclegowych)
    share_of: str | None = None
    variable_id: str


class CityStats(BaseModel):
    unit_id: str
    items: list[StatItem]
    source: str = ATTRIBUTION
    license: str = LICENSE
    url: str = "https://bdl.stat.gov.pl"
    retrieved_at: date
    from_snapshot: bool = False  # True = GUS niedostępny, pokazujemy zapisaną wcześniej kopię


Values = dict[str, dict[int, int]]  # nazwa zmiennej -> rok -> wartość


def fetch_values(unit_id: str) -> Values:
    headers = {"User-Agent": USER_AGENT}
    if client_id := os.environ.get("GUS_CLIENT_ID"):  # opcjonalny klucz - wyższe limity zapytań
        headers["X-ClientId"] = client_id
    params = [("format", "json")] + [("var-id", var) for var in VARIABLES.values()]
    resp = httpx.get(f"{BDL_URL}/data/by-unit/{unit_id}", params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    by_id = {str(res["id"]): res["values"] for res in resp.json().get("results", [])}
    return {
        name: {int(v["year"]): int(v["val"]) for v in by_id.get(var, []) if v.get("val") is not None}
        for name, var in VARIABLES.items()
    }


def _latest(values: Values, name: str) -> tuple[int, int] | None:
    series = values.get(name) or {}
    if not series:
        return None
    year = max(series)
    return year, series[year]


def _share(part: int, whole: int | None) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def build_stats(values: Values, unit_id: str, retrieved_at: date, from_snapshot: bool = False) -> CityStats:
    items: list[StatItem] = []
    population = values.get("population", {})

    def people(key: str, label: str) -> None:
        if (latest := _latest(values, key)) is None:
            return
        year, value = latest
        whole = population.get(year) or (population[max(population)] if population else None)
        items.append(StatItem(
            key=key, label=label, value=value, unit="osób", year=year,
            share_pct=_share(value, whole), share_of="mieszkańców", variable_id=VARIABLES[key],
        ))

    people("disabled", "osoby z niepełnosprawnościami (spis powszechny)")
    people("disabled_legal", "w tym z orzeczeniem o niepełnosprawności")
    people("post_working_age", "osoby w wieku poprodukcyjnym")

    lodging_total = values.get("lodging_total", {})
    for key, label in [
        ("lodging_elevator", "obiekty noclegowe z windą dla osób z niepełnosprawnością ruchową"),
        ("lodging_ramp", "obiekty noclegowe z pochylnią wjazdową"),
        ("lodging_auto_door", "obiekty noclegowe z drzwiami otwieranymi automatycznie"),
        ("lodging_parking", "obiekty noclegowe z miejscami parkingowymi dla osób z niepełnosprawnością"),
    ]:
        if (latest := _latest(values, key)) is None:
            continue
        year, value = latest
        items.append(StatItem(
            key=key, label=label, value=value, unit="obiektów", year=year,
            share_pct=_share(value, lodging_total.get(year)), share_of="obiektów noclegowych",
            variable_id=VARIABLES[key],
        ))
    return CityStats(unit_id=unit_id, items=items, retrieved_at=retrieved_at, from_snapshot=from_snapshot)


def snapshot_path(unit_id: str) -> Path:
    return SNAPSHOT_DIR / f"gus_{unit_id}.json"


def save_snapshot(unit_id: str, values: Values, retrieved_at: date) -> None:
    payload = {"unit_id": unit_id, "retrieved_at": retrieved_at.isoformat(), "values": values}
    snapshot_path(unit_id).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def load_snapshot(unit_id: str) -> tuple[Values, date] | None:
    path = snapshot_path(unit_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = {name: {int(y): v for y, v in series.items()} for name, series in payload["values"].items()}
    return values, date.fromisoformat(payload["retrieved_at"])


_cache: dict[str, tuple[float, CityStats]] = {}
_lock = threading.Lock()


def city_stats(unit_id: str | None = None) -> CityStats:
    """Dane z GUS z pamięci podręcznej (12 h); gdy GUS nie odpowiada - zapisana kopia z data/gus_<unit>.json."""
    unit_id = unit_id or os.environ.get("GUS_UNIT_ID", KRAKOW_UNIT_ID)
    with _lock:
        cached = _cache.get(unit_id)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_S:
            return cached[1]
    try:
        values = fetch_values(unit_id)
        stats = build_stats(values, unit_id, date.today())
    except (httpx.HTTPError, ValueError, KeyError):
        snapshot = load_snapshot(unit_id)
        if snapshot is None:
            raise
        values, retrieved_at = snapshot
        stats = build_stats(values, unit_id, retrieved_at, from_snapshot=True)
    with _lock:
        _cache[unit_id] = (time.monotonic(), stats)
    return stats


def stats_summary(stats: CityStats) -> dict[str, Any]:
    """Wartości po kluczu - wygodne dla frontendu i testów."""
    return {item.key: item for item in stats.items}


# ---------- bezpieczeństwo (wypadki drogowe, przestępczość) ----------

POLAND_UNIT_ID = "000000000000"

# Wskaźniki na mieszkańca - porównywalne z średnią krajową. Dostępne dla powiatu (miasto na prawach powiatu).
SAFETY_VARIABLES = {
    "accidents": "7849",                  # wypadki drogowe ogółem (P1754)
    "injured": "7851",                    # ranni (P1754)
    "killed": "7850",                     # ofiary śmiertelne (P1754)
    "accidents_per_100k": "471916",       # wypadki drogowe na 100 tys. ludności (P2423)
    "killed_per_100k": "60551",           # ofiary śmiertelne na 100 tys. ludności (P2423)
    "crimes_per_1000": "1752907",         # przestępstwa stwierdzone przez Policję na 1000 mieszkańców (P4633)
}


class SafetyIndicator(BaseModel):
    key: str
    label: str
    city: float
    country: float | None
    unit: str
    year: int
    ratio: float | None  # miasto / Polska
    variable_id: str


class CitySafety(BaseModel):
    unit_id: str
    indicators: list[SafetyIndicator]
    accidents: int | None = None
    injured: int | None = None
    killed: int | None = None
    year: int | None = None
    source: str = ATTRIBUTION
    license: str = LICENSE
    url: str = "https://bdl.stat.gov.pl"
    retrieved_at: date
    from_snapshot: bool = False


def powiat_of(unit_id: str) -> str:
    """Miasto na prawach powiatu: gmina 011212161011 -> powiat 011212161000."""
    return unit_id[:9] + "000"


def fetch_safety_values(unit_id: str) -> dict[str, Values]:
    headers = {"User-Agent": USER_AGENT}
    if client_id := os.environ.get("GUS_CLIENT_ID"):
        headers["X-ClientId"] = client_id
    params = [("format", "json")] + [("var-id", var) for var in SAFETY_VARIABLES.values()]
    result: dict[str, Values] = {}
    for scope, unit in (("city", powiat_of(unit_id)), ("country", POLAND_UNIT_ID)):
        resp = httpx.get(f"{BDL_URL}/data/by-unit/{unit}", params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        by_id = {str(res["id"]): res["values"] for res in resp.json().get("results", [])}
        result[scope] = {
            name: {int(v["year"]): float(v["val"]) for v in by_id.get(var, []) if v.get("val") is not None}
            for name, var in SAFETY_VARIABLES.items()
        }
    return result


def build_safety(values: dict[str, Values], unit_id: str, retrieved_at: date, from_snapshot: bool = False) -> CitySafety:
    city, country = values.get("city", {}), values.get("country", {})
    indicators = []
    for key, label, unit in [
        ("accidents_per_100k", "wypadki drogowe", "na 100 tys. mieszkańców"),
        ("killed_per_100k", "ofiary śmiertelne wypadków", "na 100 tys. mieszkańców"),
        ("crimes_per_1000", "przestępstwa stwierdzone przez Policję", "na 1000 mieszkańców"),
    ]:
        if (latest := _latest(city, key)) is None:
            continue
        year, value = latest
        national = country.get(key, {}).get(year)
        indicators.append(SafetyIndicator(
            key=key, label=label, city=value, country=national, unit=unit, year=year,
            ratio=round(value / national, 2) if national else None, variable_id=SAFETY_VARIABLES[key],
        ))
    totals = {key: _latest(city, key) for key in ("accidents", "injured", "killed")}
    year = totals["accidents"][0] if totals["accidents"] else None
    return CitySafety(
        unit_id=unit_id,
        indicators=indicators,
        accidents=int(totals["accidents"][1]) if totals["accidents"] else None,
        injured=int(totals["injured"][1]) if totals["injured"] else None,
        killed=int(totals["killed"][1]) if totals["killed"] else None,
        year=year,
        retrieved_at=retrieved_at,
        from_snapshot=from_snapshot,
    )


def _safety_snapshot_path(unit_id: str) -> Path:
    return SNAPSHOT_DIR / f"gus_safety_{unit_id}.json"


def save_safety_snapshot(unit_id: str, values: dict[str, Values], retrieved_at: date) -> None:
    payload = {"unit_id": unit_id, "retrieved_at": retrieved_at.isoformat(), "values": values}
    _safety_snapshot_path(unit_id).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_safety_snapshot(unit_id: str) -> tuple[dict[str, Values], date] | None:
    path = _safety_snapshot_path(unit_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = {
        scope: {name: {int(y): v for y, v in series.items()} for name, series in by_name.items()}
        for scope, by_name in payload["values"].items()
    }
    return values, date.fromisoformat(payload["retrieved_at"])


_safety_cache: dict[str, tuple[float, CitySafety]] = {}


def city_safety(unit_id: str | None = None) -> CitySafety:
    """Wskaźniki bezpieczeństwa miasta vs średnia krajowa (pamięć 12 h, przy awarii GUS - zapisana kopia)."""
    unit_id = unit_id or os.environ.get("GUS_UNIT_ID", KRAKOW_UNIT_ID)
    with _lock:
        cached = _safety_cache.get(unit_id)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_S:
            return cached[1]
    try:
        safety = build_safety(fetch_safety_values(unit_id), unit_id, date.today())
    except (httpx.HTTPError, ValueError, KeyError):
        snapshot = _load_safety_snapshot(unit_id)
        if snapshot is None:
            raise
        safety = build_safety(snapshot[0], unit_id, snapshot[1], from_snapshot=True)
    with _lock:
        _safety_cache[unit_id] = (time.monotonic(), safety)
    return safety
