"""Źródło danych: OpenStreetMap (licencja ODbL).

- wyszukiwanie miejsc: Nominatim
- cechy w otoczeniu miejsca: Overpass API

Oba serwisy mają zasady użycia (max ~1 zapytanie/s, własny User-Agent) - na produkcję
własna instancja albo cache. Mapowanie tagów: https://wiki.openstreetmap.org/wiki/Key:wheelchair
"""

from __future__ import annotations

import math
import os
import re
from datetime import date, datetime
from typing import Any

import httpx

from ..classification.fusion import haversine_m
from ..classification.models import FeatureType, GeoPoint, Observation, Source, SourceType

# Publiczne instancje bywają przeciążone - próbujemy po kolei. OVERPASS_URL nadpisuje listę (np. własna instancja).
OVERPASS_URLS = (
    [os.environ["OVERPASS_URL"]]
    if os.environ.get("OVERPASS_URL")
    else [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]
)
OVERPASS_TIMEOUT = httpx.Timeout(30, connect=8)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_LOOKUP_URL = "https://nominatim.openstreetmap.org/lookup"
USER_AGENT = "KrakowBezBarier/0.1 (HackYeah prototype)"
LICENSE = "ODbL"

# Kraków: minLon, maxLat, maxLon, minLat (format viewbox Nominatim)
KRAKOW_VIEWBOX = "19.79,50.13,20.22,49.97"

FOOTWAY_RE = "^(footway|pedestrian|path|steps|living_street)$"
ENTRANCE_LINK_RADIUS_M = 15  # wejście bliżej niż tyle od miejsca uznajemy za jego wejście

# OSM wheelchair=* na obiekcie/wejściu -> przybliżona wysokość progu (wg wiki OSM:
# yes = bez stopni, limited = pojedynczy stopień do ~7 cm, no = wyższe stopnie / schody)
WHEELCHAIR_TO_THRESHOLD_CM = {
    "yes": {"lo": 0, "hi": 2},
    "limited": {"lo": 2, "hi": 7},
    "no": {"lo": 7, "hi": 30},
}


# ---------- parsowanie wartości tagów ----------

def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    if value in ("yes", "true", "1"):
        return True
    if value in ("no", "false", "0", "none"):
        return False
    return None


def parse_length_cm(value: str | None) -> float | None:
    """'0.9' (metry domyślnie), '90 cm', '0.9 m', '3cm' -> cm."""
    if not value:
        return None
    m = re.fullmatch(r"\s*([0-9]+(?:[.,][0-9]+)?)\s*(cm|m|mm)?\s*", value)
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    unit = m.group(2) or "m"
    return round(number * {"m": 100, "cm": 1, "mm": 0.1}[unit], 1)


def parse_incline_pct(value: str | None) -> float | None:
    """'5%', '-8%', '10°' -> wartość bezwzględna w %. 'up'/'down' -> None."""
    if not value:
        return None
    m = re.fullmatch(r"\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(%|°)?\s*", value)
    if not m:
        return None
    number = abs(float(m.group(1).replace(",", ".")))
    if m.group(2) == "°":
        return round(math.tan(math.radians(number)) * 100, 1)
    return number


def parse_osm_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt, length in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(value[:length], fmt).date()
        except ValueError:
            continue
    return None


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attrs.items() if v is not None}


# ---------- mapowanie elementów OSM na obserwacje ----------

def _location(el: dict[str, Any]) -> GeoPoint | None:
    if "lat" in el:
        return GeoPoint(lat=el["lat"], lon=el["lon"])
    if "center" in el:
        return GeoPoint(lat=el["center"]["lat"], lon=el["center"]["lon"])
    return None


def _source(el: dict[str, Any], today: date) -> Source:
    tags = el.get("tags", {})
    # check_date = ostatnie potwierdzenie w terenie; inaczej data ostatniej edycji elementu
    observed = (
        parse_osm_date(tags.get("check_date"))
        or parse_osm_date(tags.get("survey:date"))
        or parse_osm_date(el.get("timestamp"))
        or today
    )
    return Source(
        type=SourceType.OSM,
        name=f"OpenStreetMap {el['type']}/{el['id']}",
        url=f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
        license=LICENSE,
        observed_at=observed,
        retrieved_at=today,
    )


def _entrance_attrs(tags: dict[str, str]) -> dict[str, Any]:
    threshold = parse_length_cm(tags.get("kerb:height"))
    attrs: dict[str, Any] = {
        "width_cm": parse_length_cm(tags.get("door:width") or tags.get("width")),
        "threshold_cm": threshold,
        "automatic_door": None if "automatic_door" not in tags else tags["automatic_door"] != "no",
    }
    if threshold is None and tags.get("wheelchair") in WHEELCHAIR_TO_THRESHOLD_CM:
        attrs["threshold_cm"] = WHEELCHAIR_TO_THRESHOLD_CM[tags["wheelchair"]]
        attrs["description"] = f"wysokość progu oszacowana z tagu OSM wheelchair={tags['wheelchair']}"
    return _clean(attrs)


def _features_from_tags(el: dict[str, Any], is_place: bool) -> list[tuple[FeatureType, dict[str, Any]]]:
    tags = el.get("tags", {})
    out: list[tuple[FeatureType, dict[str, Any]]] = []
    highway = tags.get("highway")

    if is_place:
        # Sam obiekt (kawiarnia, muzeum): tagi opisują jego wejście
        out.append((FeatureType.ENTRANCE, _entrance_attrs(tags)))
        if parse_bool(tags.get("toilets:wheelchair")):
            out.append((FeatureType.AMENITY, {"kind": "toilets_wheelchair"}))
        if tags.get("entrance:step_count") or tags.get("step_count"):
            out.append((FeatureType.STEPS, _clean({
                "count": _int(tags.get("entrance:step_count") or tags.get("step_count")),
                "ramp": parse_bool(tags.get("ramp:wheelchair") or tags.get("ramp")),
            })))
        return out

    if el["type"] == "node":
        if highway == "crossing":
            out.append((FeatureType.CROSSING, _clean({
                "kerb": tags.get("kerb") if tags.get("kerb") in ("raised", "lowered", "flush") else None,
                "kerb_height_cm": parse_length_cm(tags.get("kerb:height")),
                "tactile_paving": parse_bool(tags.get("tactile_paving")),
                "traffic_signals": tags.get("crossing") == "traffic_signals"
                or parse_bool(tags.get("crossing:signals")) is True,
                "sound_signals": parse_bool(tags.get("traffic_signals:sound")),
            })))
        elif tags.get("barrier") == "kerb" or "kerb" in tags:
            out.append((FeatureType.KERB, _clean({
                "kind": tags.get("kerb") if tags.get("kerb") in ("raised", "lowered", "flush") else None,
                "height_cm": parse_length_cm(tags.get("kerb:height")),
            })))
        if "entrance" in tags:
            out.append((FeatureType.ENTRANCE, _entrance_attrs(tags)))
            if tags.get("step_count"):
                out.append((FeatureType.STEPS, _clean({
                    "count": _int(tags.get("step_count")),
                    "ramp": parse_bool(tags.get("ramp:wheelchair") or tags.get("ramp")),
                })))
        if tags.get("amenity") == "bench":
            out.append((FeatureType.AMENITY, {"kind": "bench"}))
        if tags.get("amenity") == "toilets" and parse_bool(tags.get("wheelchair")):
            out.append((FeatureType.AMENITY, {"kind": "toilets_wheelchair"}))
        if highway == "elevator":
            out.append((FeatureType.AMENITY, {"kind": "elevator"}))
        return out

    # Drogi piesze (way)
    if highway == "steps":
        handrail = parse_bool(tags.get("handrail"))
        if handrail is None and any(parse_bool(tags.get(f"handrail:{s}")) for s in ("left", "right", "center")):
            handrail = True
        out.append((FeatureType.STEPS, _clean({
            "count": _int(tags.get("step_count")),
            "handrail": handrail,
            "ramp": parse_bool(tags.get("ramp:wheelchair") or tags.get("ramp")),
        })))
    if tags.get("surface"):
        out.append((FeatureType.SURFACE, {"value": tags["surface"]}))
    if (width := parse_length_cm(tags.get("width"))) is not None:
        out.append((FeatureType.PATH_WIDTH, {"width_cm": width}))
    if (incline := parse_incline_pct(tags.get("incline"))) is not None:
        out.append((FeatureType.INCLINE, {"incline_pct": incline}))
    return out


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_overpass(
    data: dict[str, Any],
    today: date,
    place_id: str | None = None,
    place_location: GeoPoint | None = None,
    place_osm: tuple[str, int] | None = None,
) -> list[Observation]:
    """Odpowiedź Overpass -> obserwacje.

    Jeśli podano miejsce: tagi samego obiektu i wejścia w promieniu ENTRANCE_LINK_RADIUS_M
    dostają place_id; pozostałe wejścia (do sąsiednich budynków) są pomijane.
    """
    observations: list[Observation] = []
    for el in data.get("elements", []):
        location = _location(el)
        if location is None:
            continue
        is_place = place_osm is not None and (el["type"], el["id"]) == place_osm
        for ftype, attrs in _features_from_tags(el, is_place):
            obs_place_id = place_id if is_place else None
            if ftype == FeatureType.ENTRANCE and not is_place:
                if place_location is None or haversine_m(location, place_location) > ENTRANCE_LINK_RADIUS_M:
                    continue
                obs_place_id = place_id
            observations.append(Observation(
                id=f"osm-{el['type']}-{el['id']}-{ftype.value}",
                type=ftype,
                attrs=attrs,
                location=location if not is_place or place_location is None else place_location,
                source=_source(el, today),
                place_id=obs_place_id,
            ))
    return observations


# ---------- zapytania sieciowe ----------

def overpass_query(lat: float, lon: float, radius_m: float, place_osm: tuple[str, int] | None = None) -> str:
    around = f"(around:{radius_m:g},{lat},{lon})"
    place = f"{place_osm[0]}(id:{place_osm[1]});" if place_osm else ""
    return f"""[out:json][timeout:25];
(
  {place}
  node{around}["barrier"="kerb"];
  node{around}["kerb"];
  node{around}["highway"="crossing"];
  node{around}["entrance"];
  node{around}["amenity"="bench"];
  node{around}["amenity"="toilets"];
  node{around}["highway"="elevator"];
  way{around}["highway"~"{FOOTWAY_RE}"];
);
out center meta;"""


def fetch_place_observations(
    place_id: str,
    location: GeoPoint,
    place_osm: tuple[str, int] | None,
    radius_m: float = 30,
    today: date | None = None,
) -> list[Observation]:
    today = today or date.today()
    query = overpass_query(location.lat, location.lon, radius_m, place_osm)
    last_error: httpx.HTTPError | None = None
    for url in OVERPASS_URLS:
        try:
            resp = httpx.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=OVERPASS_TIMEOUT)
            resp.raise_for_status()
            return parse_overpass(resp.json(), today, place_id, location, place_osm)
        except httpx.HTTPError as e:
            last_error = e
    assert last_error is not None
    raise last_error


def search_places(query: str, viewbox: str = KRAKOW_VIEWBOX, limit: int = 10) -> list[dict[str, Any]]:
    """Zwraca surowe wyniki Nominatim (ograniczone do viewbox miasta)."""
    resp = httpx.get(
        NOMINATIM_URL,
        params={
            "q": query,
            "format": "jsonv2",
            "limit": limit,
            "viewbox": viewbox,
            "bounded": 1,
            "accept-language": "pl",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def lookup_place(osm_type: str, osm_id: int) -> dict[str, Any] | None:
    """Jedno miejsce po identyfikatorze OSM (Nominatim lookup) - ten sam format co wyniki search_places."""
    resp = httpx.get(
        NOMINATIM_LOOKUP_URL,
        params={"osm_ids": f"{osm_type[0].upper()}{osm_id}", "format": "jsonv2", "accept-language": "pl"},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resp.raise_for_status()
    results = resp.json()
    return results[0] if results else None
