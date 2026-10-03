from datetime import date

import httpx
import pytest
from conftest import TODAY

from bezbarier.classification import FeatureType, GeoPoint, SourceType
from bezbarier.sources import osm
from bezbarier.sources.osm import parse_incline_pct, parse_length_cm, parse_osm_date, parse_overpass

PLACE = GeoPoint(lat=50.0617, lon=19.9373)


@pytest.mark.parametrize(
    ("raw", "cm"),
    [("0.9", 90), ("90 cm", 90), ("0,85 m", 85), ("3cm", 3), ("abc", None), (None, None)],
)
def test_parse_length(raw, cm):
    assert parse_length_cm(raw) == cm


@pytest.mark.parametrize(("raw", "pct"), [("5%", 5), ("-8%", 8), ("up", None), ("45°", 100)])
def test_parse_incline(raw, pct):
    assert parse_incline_pct(raw) == pct


def test_parse_date():
    assert parse_osm_date("2024-03-15T10:00:00Z") == date(2024, 3, 15)
    assert parse_osm_date("2023-07") == date(2023, 7, 1)


OVERPASS = {
    "elements": [
        {  # sam obiekt (kawiarnia) - tagi opisują wejście
            "type": "node", "id": 1, "lat": 50.0617, "lon": 19.9373, "timestamp": "2025-01-10T00:00:00Z",
            "tags": {"amenity": "cafe", "name": "Kawiarnia", "wheelchair": "limited", "toilets:wheelchair": "yes"},
        },
        {  # obniżony krawężnik z check_date
            "type": "node", "id": 2, "lat": 50.06172, "lon": 19.93732, "timestamp": "2020-01-01T00:00:00Z",
            "tags": {"barrier": "kerb", "kerb": "lowered", "check_date": "2026-05-01"},
        },
        {  # przejście
            "type": "node", "id": 3, "lat": 50.06175, "lon": 19.9374, "timestamp": "2024-06-01T00:00:00Z",
            "tags": {"highway": "crossing", "crossing": "traffic_signals", "tactile_paving": "yes",
                     "traffic_signals:sound": "no"},
        },
        {  # wejście tuż obok miejsca
            "type": "node", "id": 4, "lat": 50.06171, "lon": 19.93731, "timestamp": "2024-06-01T00:00:00Z",
            "tags": {"entrance": "main", "door:width": "0.8"},
        },
        {  # wejście do sąsiedniego budynku (>15 m) - pomijane
            "type": "node", "id": 5, "lat": 50.0620, "lon": 19.9373, "timestamp": "2024-06-01T00:00:00Z",
            "tags": {"entrance": "yes"},
        },
        {  # chodnik
            "type": "way", "id": 6, "center": {"lat": 50.06168, "lon": 19.93728}, "timestamp": "2023-03-01T00:00:00Z",
            "tags": {"highway": "footway", "footway": "sidewalk", "surface": "sett", "width": "2"},
        },
        {  # schody z poręczą po prawej
            "type": "way", "id": 7, "center": {"lat": 50.0616, "lon": 19.9372}, "timestamp": "2023-03-01T00:00:00Z",
            "tags": {"highway": "steps", "step_count": "4", "handrail:right": "yes"},
        },
    ]
}


def _parse():
    return parse_overpass(OVERPASS, TODAY, place_id="osm-node-1", place_location=PLACE, place_osm=("node", 1))


def test_all_relevant_features_extracted():
    obs = {o.id: o for o in _parse()}
    assert set(obs) == {
        "osm-node-1-entrance",
        "osm-node-1-amenity",
        "osm-node-2-kerb",
        "osm-node-3-crossing",
        "osm-node-4-entrance",
        "osm-way-6-surface",
        "osm-way-6-path_width",
        "osm-way-7-steps",
    }
    assert all(o.source.type == SourceType.OSM and o.source.license == "ODbL" for o in obs.values())


def test_place_tags_become_entrance_of_place():
    obs = {o.id: o for o in _parse()}
    entrance = obs["osm-node-1-entrance"]
    assert entrance.place_id == "osm-node-1"
    assert entrance.attrs["threshold_cm"] == {"lo": 2, "hi": 7}  # z wheelchair=limited
    assert obs["osm-node-1-amenity"].attrs == {"kind": "toilets_wheelchair"}
    assert obs["osm-node-4-entrance"].place_id == "osm-node-1"
    assert obs["osm-node-4-entrance"].attrs == {"width_cm": 80}


def test_check_date_preferred_over_edit_timestamp():
    obs = {o.id: o for o in _parse()}
    assert obs["osm-node-2-kerb"].source.observed_at == date(2026, 5, 1)
    assert obs["osm-node-3-crossing"].source.observed_at == date(2024, 6, 1)


def test_tag_mapping():
    obs = {o.id: o for o in _parse()}
    assert obs["osm-node-2-kerb"].attrs == {"kind": "lowered"}
    assert obs["osm-node-3-crossing"].attrs == {"tactile_paving": True, "traffic_signals": True, "sound_signals": False}
    assert obs["osm-way-6-surface"].attrs == {"value": "sett"}
    assert obs["osm-way-6-path_width"].attrs == {"width_cm": 200}
    assert obs["osm-way-7-steps"].type == FeatureType.STEPS
    assert obs["osm-way-7-steps"].attrs == {"count": 4, "handrail": True}


def _response(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {}, request=httpx.Request("POST", "https://overpass.test"))


def test_overpass_query_uses_bbox():
    query = osm.overpass_query(50.0617, 19.9373, 30, ("way", 26195267))
    assert "around" not in query
    assert "way(id:26195267);" in query
    assert "node(50.061431,19.936880,50.061969,19.937720)" in query  # ok. 30 m w każdą stronę


def test_first_successful_server_wins(monkeypatch):
    """Serwery pytane równolegle - jeden odrzuca (limit), drugi odpowiada: bierzemy odpowiedź."""
    def post(url, **kwargs):
        if "busy" in url:
            return _response(429)
        return _response(200, OVERPASS)

    monkeypatch.setattr(osm, "PUBLIC_OVERPASS_URLS", ["https://busy.test", "https://ok.test"])
    monkeypatch.setattr(osm.httpx, "post", post)
    monkeypatch.delenv("OVERPASS_URL", raising=False)
    obs = osm.fetch_place_observations("osm-node-1", PLACE, ("node", 1), today=TODAY)
    assert len(obs) == 8


def test_overpass_gives_up_with_readable_reason(monkeypatch):
    monkeypatch.setattr(osm.httpx, "post", lambda *a, **k: _response(429))
    monkeypatch.setenv("OVERPASS_URL", "https://overpass.test")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        osm.fetch_place_observations("osm-node-1", PLACE, ("node", 1), today=TODAY)
    assert osm.describe_error(exc.value) == "serwer ogranicza liczbę zapytań"


def test_overpass_stops_after_total_budget(monkeypatch):
    import time

    def slow_post(*args, **kwargs):
        time.sleep(0.5)  # serwer "wisi" dłużej niż łączny limit
        return _response(200, OVERPASS)

    monkeypatch.setattr(osm, "OVERPASS_TOTAL_BUDGET_S", 0.1)
    monkeypatch.setattr(osm.httpx, "post", slow_post)
    monkeypatch.setenv("OVERPASS_URL", "https://overpass.test")
    started = time.monotonic()
    with pytest.raises(httpx.TimeoutException):
        osm.fetch_place_observations("osm-node-1", PLACE, ("node", 1), today=TODAY)
    assert time.monotonic() - started < 0.4  # nie czekamy na wolny serwer
