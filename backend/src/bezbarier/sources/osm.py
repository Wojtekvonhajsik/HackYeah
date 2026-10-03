"""Źródło danych: OpenStreetMap (licencja ODbL).

- wyszukiwanie miejsc: Nominatim
- cechy w otoczeniu miejsca: Overpass API

Oba serwisy mają zasady użycia (max ~1 zapytanie/s, własny User-Agent) - na produkcję
własna instancja albo cache. Mapowanie tagów: https://wiki.openstreetmap.org/wiki/Key:wheelchair
"""

from __future__ import annotations

import logging
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree

import httpx

from ..classification.fusion import haversine_m
from ..classification.models import FeatureType, GeoPoint, Observation, Source, SourceType

# Publiczne instancje bywają przeciążone - próbujemy po kolei. OVERPASS_URL nadpisuje listę (np. własna instancja).
PUBLIC_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
# Ile czekamy na Overpass (wszystkie serwery równolegle), zanim spróbujemy głównego API OpenStreetMap
OVERPASS_BUDGET_S = 12
OSM_API_URL = "https://api.openstreetmap.org/api/0.6"
logger = logging.getLogger(__name__)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_LOOKUP_URL = "https://nominatim.openstreetmap.org/lookup"
USER_AGENT = "KrakowBezBarier/0.1 (HackYeah prototype)"
LICENSE = "ODbL"

# Obszar wyszukiwania miejsc: minLon,maxLat,maxLon,minLat (format viewbox Nominatim).
# Domyślnie Kraków; inne miasto = inna wartość CITY_VIEWBOX, reszta systemu bez zmian.
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
        # Wejście blisko miejsca = wejście do miejsca: jego tagi (szerokość, stopnie) opisują samo miejsce
        is_place_entrance = (
            not is_place
            and "entrance" in el.get("tags", {})
            and place_location is not None
            and haversine_m(location, place_location) <= ENTRANCE_LINK_RADIUS_M
        )
        for ftype, attrs in _features_from_tags(el, is_place):
            obs_place_id = place_id if is_place or is_place_entrance else None
            if ftype == FeatureType.ENTRANCE and obs_place_id is None:
                continue  # wejście do sąsiedniego budynku
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

class OsmSourceError(httpx.HTTPError):
    """Serwer odpowiedział, ale nie danymi (np. strona błędu albo przerwane zapytanie Overpass)."""


def _bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """(południe, zachód, północ, wschód) prostokąta ok. radius_m w każdą stronę."""
    d_lat = radius_m / 111_320
    d_lon = radius_m / (111_320 * math.cos(math.radians(lat)))
    return lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon


def overpass_query(lat: float, lon: float, radius_m: float, place_osm: tuple[str, int] | None = None) -> str:
    # Prostokąt zamiast okręgu (around) - dla Overpass dużo tańszy, więc odpowiedź przychodzi szybciej
    s, w, n, e = _bbox(lat, lon, radius_m)
    bbox = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    place = f"{place_osm[0]}(id:{place_osm[1]});" if place_osm else ""
    return f"""[out:json][timeout:25];
(
  {place}
  node{bbox}["barrier"="kerb"];
  node{bbox}["kerb"];
  node{bbox}["highway"="crossing"];
  node{bbox}["entrance"];
  node{bbox}["amenity"~"^(bench|toilets)$"];
  node{bbox}["highway"="elevator"];
  way{bbox}["highway"~"{FOOTWAY_RE}"];
);
out center meta;"""


def _post_overpass(url: str, query: str, timeout_s: float) -> dict[str, Any]:
    timeout = httpx.Timeout(timeout_s, connect=min(8, timeout_s))
    resp = httpx.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:  # przeciążony serwer potrafi odesłać stronę HTML z kodem 200
        raise OsmSourceError("Overpass zwrócił niepoprawną odpowiedź") from e
    remark = str(data.get("remark", ""))
    if not data.get("elements") and ("error" in remark or "timed out" in remark):
        raise OsmSourceError(f"Overpass przerwał zapytanie: {remark[:80]}")
    return data


def _race_overpass(query: str, budget_s: float) -> dict[str, Any]:
    """Pyta wszystkie serwery Overpass naraz i bierze pierwszą udaną odpowiedź."""
    urls = [os.environ["OVERPASS_URL"]] if os.environ.get("OVERPASS_URL") else PUBLIC_OVERPASS_URLS
    pool = ThreadPoolExecutor(max_workers=len(urls), thread_name_prefix="overpass")
    futures = [pool.submit(_post_overpass, url, query, budget_s) for url in urls]
    last_error: httpx.HTTPError | None = None
    try:
        for future in as_completed(futures, timeout=budget_s):
            try:
                return future.result()
            except httpx.HTTPError as e:
                last_error = e
    except FuturesTimeout:
        raise last_error or httpx.TimeoutException("Przekroczony czas pobierania z Overpass") from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # nie czekamy na wolniejsze serwery
    assert last_error is not None
    raise last_error


def _relevant_node(tags: dict[str, str]) -> bool:
    return (
        tags.get("barrier") == "kerb"
        or "kerb" in tags
        or "entrance" in tags
        or tags.get("highway") in ("crossing", "elevator")
        or tags.get("amenity") in ("bench", "toilets")
    )


def fetch_osm_api(lat: float, lon: float, radius_m: float, place_osm: tuple[str, int] | None) -> dict[str, Any]:
    """Zapas, gdy Overpass nie odpowiada: główne API OpenStreetMap (jedno małe zapytanie o prostokąt ~60 m).

    Zwraca dane w tym samym formacie co Overpass, więc dalsze przetwarzanie się nie zmienia.
    API OSM służy głównie do edycji - używamy go tylko awaryjnie, dla jednego miejsca naraz.
    """
    s, w, n, e = _bbox(lat, lon, radius_m)
    headers = {"User-Agent": USER_AGENT}
    resp = httpx.get(OSM_API_URL + "/map", params={"bbox": f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}"}, headers=headers, timeout=15)
    resp.raise_for_status()
    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError as err:
        raise OsmSourceError("API OpenStreetMap zwróciło niepoprawną odpowiedź") from err

    def tags_of(el: ElementTree.Element) -> dict[str, str]:
        return {t.get("k"): t.get("v") for t in el.findall("tag")}

    coords: dict[int, tuple[float, float]] = {}
    elements: list[dict[str, Any]] = []
    for node in root.iter("node"):
        node_id = int(node.get("id"))
        coords[node_id] = (float(node.get("lat")), float(node.get("lon")))
        tags = tags_of(node)
        if tags and (_relevant_node(tags) or place_osm == ("node", node_id)):
            elements.append({
                "type": "node", "id": node_id, "lat": coords[node_id][0], "lon": coords[node_id][1],
                "timestamp": node.get("timestamp"), "tags": tags,
            })
    for way in root.iter("way"):
        way_id = int(way.get("id"))
        tags = tags_of(way)
        if not (place_osm == ("way", way_id) or re.match(FOOTWAY_RE, tags.get("highway", ""))):
            continue
        points = [coords[int(nd.get("ref"))] for nd in way.findall("nd") if int(nd.get("ref")) in coords]
        if not points:
            continue
        center = {"lat": sum(p[0] for p in points) / len(points), "lon": sum(p[1] for p in points) / len(points)}
        elements.append({"type": "way", "id": way_id, "center": center, "timestamp": way.get("timestamp"), "tags": tags})
    if place_osm and place_osm[0] == "relation":
        # relacja (np. duży kompleks) - tagi z osobnego zapytania, położenie z wyszukiwarki
        rel = httpx.get(f"{OSM_API_URL}/relation/{place_osm[1]}", headers=headers, timeout=15)
        rel.raise_for_status()
        rel_el = ElementTree.fromstring(rel.content).find("relation")
        if rel_el is not None:
            elements.append({
                "type": "relation", "id": place_osm[1], "center": {"lat": lat, "lon": lon},
                "timestamp": rel_el.get("timestamp"), "tags": tags_of(rel_el),
            })
    return {"elements": elements}


def fetch_place_observations(
    place_id: str,
    location: GeoPoint,
    place_osm: tuple[str, int] | None,
    radius_m: float = 30,
    today: date | None = None,
) -> list[Observation]:
    """Najpierw Overpass (równolegle z kilku serwerów), a gdy nie odpowie w OVERPASS_BUDGET_S -
    główne API OpenStreetMap. Publiczne serwery Overpass bywają przeciążone i potrafią nie odpowiadać
    całymi godzinami (tak było np. dla Urzędu Miasta Krakowa)."""
    today = today or date.today()
    query = overpass_query(location.lat, location.lon, radius_m, place_osm)
    try:
        data = _race_overpass(query, OVERPASS_BUDGET_S)
    except httpx.HTTPError as overpass_error:
        logger.warning("Overpass nieudany dla %s (%r) - próbuję API OpenStreetMap", place_id, overpass_error)
        data = fetch_osm_api(location.lat, location.lon, radius_m, place_osm)
    return parse_overpass(data, today, place_id, location, place_osm)


def describe_error(e: httpx.HTTPError) -> str:
    """Krótki opis błędu do komunikatu dla użytkownika."""
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 429:
            return "serwer ogranicza liczbę zapytań"
        return f"błąd serwera HTTP {e.response.status_code}"
    if isinstance(e, httpx.TimeoutException):
        return "serwer nie odpowiedział na czas"
    if isinstance(e, OsmSourceError):
        return "serwer zwrócił błąd"
    return "brak połączenia"


def search_places(query: str, viewbox: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Zwraca surowe wyniki Nominatim (ograniczone do obszaru miasta - CITY_VIEWBOX)."""
    viewbox = viewbox or os.environ.get("CITY_VIEWBOX", KRAKOW_VIEWBOX)
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
