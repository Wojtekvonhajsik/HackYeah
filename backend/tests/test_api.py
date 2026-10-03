import httpx
import pytest
from conftest import make_obs
from fastapi.testclient import TestClient

from bezbarier.api import main, security
from bezbarier.storage import InMemoryRepository

client = TestClient(main.app)

TODAY = {"today": "2026-10-03"}


@pytest.fixture(autouse=True)
def fresh_repo(monkeypatch):
    monkeypatch.setattr(main, "repo", InMemoryRepository.from_file(main.SAMPLE_DATA))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(security, "_vote_limiter", None)


def test_root_redirects_to_docs():
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/docs"


def test_cors():
    resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert resp.headers["access-control-allow-origin"] == "*"


def test_presets_have_need_based_labels():
    resp = client.get("/presets")
    assert resp.status_code == 200
    presets = resp.json()
    assert presets["step_free_strict"]["label"] == "Bez stopni, tylko płaskie przejścia"
    assert presets["non_visual"]["needs"]["vision"] is not None


def test_sample_place_with_conflict():
    resp = client.post("/places/kawiarnia-rynek/assessment", json={"preset": "step_free_strict", **TODAY})
    assert resp.status_code == 200
    body = resp.json()
    # sprzeczne, słabe źródła o krawężniku -> "do sprawdzenia", a nie "nie do pokonania"
    assert body["summary"] == "difficulties"
    assert body["contains_sample_data"] is True
    kerb = next(f for f in body["features"] if f["type"] == "kerb")
    assert kerb["conflict"] is True
    assert kerb["scope"] == "place"
    assert kerb["verdict"] == "uncertain"
    assert 0 < kerb["confidence_pct"] < 30
    surface = next(f for f in body["features"] if f["type"] == "surface")
    assert surface["scope"] == "surroundings"


def test_sample_place_without_entrance_data():
    resp = client.post("/places/muzeum-kazimierz/assessment", json={"preset": "step_free_strict", **TODAY})
    assert resp.json()["summary"] == "incomplete_data"


def test_classify_requires_profile():
    resp = client.post("/classify", json={"observations": []})
    assert resp.status_code == 400


def test_unknown_preset():
    resp = client.post("/classify", json={"observations": [], "preset": "nie-ma"})
    assert resp.status_code == 400


def test_analyze_with_mock_detector():
    image = {
        "id": "test-1",
        "provider": "user_upload",
        "location": {"lat": 50.0617, "lon": 19.9373},
        "captured_at": "2026-09-01",
    }
    resp = client.post("/observations/analyze", json={"image": image})
    assert resp.status_code == 200
    observations = resp.json()
    assert {o["type"] for o in observations} == {"kerb", "surface"}
    assert all(o["source"]["type"] == "ai_detection" and o["source"]["sample"] for o in observations)


NOMINATIM_RESULT = [{
    "osm_type": "node", "osm_id": 123, "lat": "50.0617", "lon": "19.9373",
    "name": "Kawiarnia Testowa", "display_name": "Kawiarnia Testowa, Rynek Główny, Kraków",
}]


def test_search_and_assess_osm_place(monkeypatch):
    monkeypatch.setattr(main.osm, "search_places", lambda q: NOMINATIM_RESULT)
    calls = []

    def fake_fetch(place_id, location, place_osm):
        calls.append(place_osm)
        return [make_obs("osm-e", "entrance", {"width_cm": 100, "threshold_cm": 0}, place_id=place_id)]

    monkeypatch.setattr(main.osm, "fetch_place_observations", fake_fetch)

    found = client.get("/places/search", params={"q": "kawiarnia"}).json()
    assert found[0]["id"] == "osm-node-123"
    assert found[0]["data_loaded"] is False

    body = client.post("/places/osm-node-123/assessment", json={"preset": "step_free_strict", **TODAY}).json()
    assert any(f["type"] == "entrance" and f["verdict"] == "ok" for f in body["features"])
    client.post("/places/osm-node-123/assessment", json={"preset": "stroller", **TODAY})
    assert calls == [("node", 123)]  # dane pobrane tylko raz


def test_osm_unavailable_gives_warning_not_error(monkeypatch):
    far_away = [{**NOMINATIM_RESULT[0], "lat": "50.07", "lon": "19.95"}]  # z dala od danych przykładowych
    monkeypatch.setattr(main.osm, "search_places", lambda q: far_away)

    def broken_fetch(*args):
        raise httpx.ConnectError("brak sieci")

    monkeypatch.setattr(main.osm, "fetch_place_observations", broken_fetch)
    client.get("/places/search", params={"q": "kawiarnia"})
    body = client.post("/places/osm-node-123/assessment", json={"preset": "step_free_strict", **TODAY}).json()
    assert body["summary"] == "incomplete_data"
    assert "OpenStreetMap" in body["warnings"][0]


def test_search_unavailable(monkeypatch):
    def broken(q):
        raise httpx.ConnectError("brak sieci")

    monkeypatch.setattr(main.osm, "search_places", broken)
    assert client.get("/places/search", params={"q": "x"}).status_code == 503


def test_vote_and_correction():
    resp = client.post("/observations/mly-1001-0/votes", json={
        "voter_id": "device-1", "value": "deny", "correction_attrs": {"kind": "lowered"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["observation"]["denials"] == 1
    assert body["correction"]["source"]["type"] == "user_report"

    assessment = client.post("/places/kawiarnia-rynek/assessment", json={"preset": "step_free_strict", **TODAY}).json()
    kerb = next(f for f in assessment["features"] if f["type"] == "kerb")
    assert {e["observation_id"] for e in kerb["evidence"]} >= {"mly-1001-0", body["correction"]["id"]}


def test_vote_errors():
    assert client.post("/observations/nie-ma/votes", json={"voter_id": "d", "value": "confirm"}).status_code == 404
    resp = client.post("/observations/mly-1001-0/votes", json={
        "voter_id": "d", "value": "confirm", "correction_attrs": {"kind": "lowered"},
    })
    assert resp.status_code == 400


def test_real_place_does_not_mix_in_sample_data(monkeypatch):
    # miejsce z OSM leży dokładnie przy przykładowej kawiarni
    monkeypatch.setattr(main.osm, "search_places", lambda q: NOMINATIM_RESULT)
    monkeypatch.setattr(main.osm, "fetch_place_observations", lambda *a: [])
    client.get("/places/search", params={"q": "kawiarnia"})
    body = client.post("/places/osm-node-123/assessment", json={"preset": "step_free_strict", **TODAY}).json()
    assert body["contains_sample_data"] is False
    assert body["features"] == []


def _scan_images(lat, lon, radius_m, limit):
    from datetime import date

    from bezbarier.detection import ImageRef

    return [ImageRef(id="m1", provider="mapillary", location={"lat": 50.05095, "lon": 19.944},
                     heading=0, captured_at=date(2025, 7, 1))]


def test_scan_place(monkeypatch):
    monkeypatch.setattr(main.mapillary, "images_near", _scan_images)
    resp = client.post("/places/muzeum-kazimierz/scan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["images_analyzed"] == 1
    assert {o["type"] for o in body["observations"]} == {"kerb", "surface"}
    # drugi skan nie analizuje tego samego zdjęcia
    assert client.post("/places/muzeum-kazimierz/scan").json()["images_skipped"] == 1
    assessment = client.post("/places/muzeum-kazimierz/assessment", json={"preset": "step_free_strict", **TODAY}).json()
    assert any(f["type"] == "kerb" for f in assessment["features"])


def test_scan_without_token(monkeypatch):
    monkeypatch.delenv("MAPILLARY_TOKEN", raising=False)
    resp = client.post("/places/muzeum-kazimierz/scan")
    assert resp.status_code == 400
    assert "MAPILLARY_TOKEN" in resp.json()["detail"]


def test_missing_detector_library_gives_clear_error(monkeypatch):
    def broken():
        raise ImportError("No module named 'google'")

    monkeypatch.setenv("DETECTOR", "gemini")
    monkeypatch.setattr(main, "get_detector", broken)
    monkeypatch.setattr(main.mapillary, "images_near", _scan_images)
    resp = client.post("/places/muzeum-kazimierz/scan")
    assert resp.status_code == 500
    assert 'pip install -e ".[gemini]"' in resp.json()["detail"]


def test_missing_api_key_gives_clear_error(monkeypatch):
    def no_key():
        raise ValueError("No API key was provided.")

    monkeypatch.setattr(main, "get_detector", no_key)
    monkeypatch.setattr(main.mapillary, "images_near", _scan_images)
    resp = client.post("/places/muzeum-kazimierz/scan")
    assert resp.status_code == 400
    assert "No API key" in resp.json()["detail"]


def test_osm_place_restored_after_restart(monkeypatch):
    """Po restarcie serwera repo jest puste - miejsce osm-* odtwarzamy z OSM zamiast zwracać 404."""
    lookups = []

    def lookup(osm_type, osm_id):
        lookups.append((osm_type, osm_id))
        return {**NOMINATIM_RESULT[0], "lat": "50.07", "lon": "19.95"}

    monkeypatch.setattr(main.osm, "lookup_place", lookup)
    monkeypatch.setattr(main.osm, "fetch_place_observations", lambda *a: [])
    resp = client.post("/places/osm-node-123/assessment", json={"preset": "step_free_strict", **TODAY})
    assert resp.status_code == 200
    assert lookups == [("node", 123)]
    assert client.get("/places").json()[-1]["name"] == "Kawiarnia Testowa"


def test_unknown_place_id():
    resp = client.post("/places/nie-ma/assessment", json={"preset": "step_free_strict"})
    assert resp.status_code == 404


def test_scan_expands_radius_when_no_images(monkeypatch):
    radii = []

    def images_near(lat, lon, radius_m, limit):
        radii.append(radius_m)
        return _scan_images(lat, lon, radius_m, limit) if radius_m >= 100 else []

    monkeypatch.setattr(main.mapillary, "images_near", images_near)
    body = client.post("/places/muzeum-kazimierz/scan").json()
    assert radii == [25, 50, 100]
    assert body["radius_m"] == 100
    assert body["images_found"] == 1


def test_scan_gives_up_at_max_radius(monkeypatch):
    monkeypatch.setattr(main.mapillary, "images_near", lambda lat, lon, radius_m, limit: [])
    body = client.post("/places/muzeum-kazimierz/scan").json()
    assert (body["images_found"], body["radius_m"]) == (0, 100)


def test_scan_requires_admin_token_when_configured(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "sekret")
    monkeypatch.setattr(main.mapillary, "images_near", _scan_images)
    assert client.post("/places/muzeum-kazimierz/scan").status_code == 401
    assert client.post("/places/muzeum-kazimierz/scan", headers={"X-Admin-Token": "zly"}).status_code == 401
    assert client.post("/places/muzeum-kazimierz/scan", headers={"X-Admin-Token": "sekret"}).status_code == 200
    image = {"id": "x", "provider": "user_upload", "location": {"lat": 50.0, "lon": 19.9}, "captured_at": "2026-09-01"}
    assert client.post("/observations/analyze", json={"image": image}).status_code == 401


def test_vote_rate_limit(monkeypatch):
    monkeypatch.setenv("VOTE_RATE_LIMIT_PER_HOUR", "2")
    for voter in ("a", "b"):
        assert client.post("/observations/mly-1001-0/votes", json={"voter_id": voter, "value": "confirm"}).status_code == 200
    resp = client.post("/observations/mly-1001-0/votes", json={"voter_id": "c", "value": "confirm"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def _good_entrance(place_id, location, place_osm):
    return [make_obs("osm-e", "entrance", {"width_cm": 100, "threshold_cm": 0}, place_id=place_id)]


def test_osm_data_refreshed_after_30_days(monkeypatch):
    far_away = [{**NOMINATIM_RESULT[0], "lat": "50.07", "lon": "19.95"}]
    monkeypatch.setattr(main.osm, "search_places", lambda q: far_away)
    calls = []

    def fetch(*args):
        calls.append(args)
        return _good_entrance(*args)

    monkeypatch.setattr(main.osm, "fetch_place_observations", fetch)
    client.get("/places/search", params={"q": "kawiarnia"})

    def assess_on(day):
        return client.post("/places/osm-node-123/assessment", json={"preset": "step_free_strict", "today": day}).json()

    assess_on("2026-10-03")
    assess_on("2026-10-20")
    assert len(calls) == 1  # w ciągu 30 dni z pamięci
    assess_on("2026-11-05")
    assert len(calls) == 2  # po 30 dniach odświeżone


def test_failed_refresh_keeps_old_data_without_downgrading(monkeypatch):
    far_away = [{**NOMINATIM_RESULT[0], "lat": "50.07", "lon": "19.95"}]
    monkeypatch.setattr(main.osm, "search_places", lambda q: far_away)
    monkeypatch.setattr(main.osm, "fetch_place_observations", _good_entrance)
    client.get("/places/search", params={"q": "kawiarnia"})
    first = client.post("/places/osm-node-123/assessment", json={"preset": "step_free_strict", "today": "2026-10-03"}).json()

    def broken(*args):
        raise httpx.ConnectError("brak sieci")

    monkeypatch.setattr(main.osm, "fetch_place_observations", broken)
    later = client.post("/places/osm-node-123/assessment", json={"preset": "step_free_strict", "today": "2026-12-01"}).json()
    assert later["summary"] == first["summary"]
    assert "odświeżyć" in later["warnings"][0]


def test_scan_with_empty_token(monkeypatch):
    monkeypatch.setenv("MAPILLARY_TOKEN", "")
    resp = client.post("/places/muzeum-kazimierz/scan")
    assert resp.status_code == 400
    assert "Brak MAPILLARY_TOKEN" in resp.json()["detail"]


def test_scan_with_rejected_token(monkeypatch):
    def rejected(*args, **kwargs):
        request = httpx.Request("GET", "https://graph.mapillary.com/images")
        raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))

    monkeypatch.setattr(main.mapillary, "images_near", rejected)
    resp = client.post("/places/muzeum-kazimierz/scan")
    assert resp.status_code == 400
    assert "odrzuciło" in resp.json()["detail"]


def test_scan_results_from_expanded_radius_appear_in_assessment(monkeypatch):
    """Muzeum Narodowe: zdjęcia 30-50 m od punktu miejsca - wyniki muszą trafić do jego oceny."""
    from datetime import date

    from bezbarier.detection import ImageRef

    def images_near(lat, lon, radius_m, limit):
        if radius_m < 50:
            return []
        return [ImageRef(id="m-far", provider="mapillary", location={"lat": 50.0514, "lon": 19.944},
                         heading=None, captured_at=date(2025, 7, 1))]  # ~45 m od muzeum

    monkeypatch.setattr(main.mapillary, "images_near", images_near)
    scan = client.post("/places/muzeum-kazimierz/scan").json()
    assert scan["images_analyzed"] == 1
    assessment = client.post("/places/muzeum-kazimierz/assessment", json={"preset": "step_free_strict", **TODAY}).json()
    evidence_ids = {e["observation_id"] for f in assessment["features"] for e in f["evidence"]}
    assert {"mapillary-m-far-0", "mapillary-m-far-1"} <= evidence_ids


def test_osm_failure_reason_in_warning(monkeypatch):
    far_away = [{**NOMINATIM_RESULT[0], "lat": "50.07", "lon": "19.95"}]
    monkeypatch.setattr(main.osm, "search_places", lambda q: far_away)

    def rate_limited(*args):
        request = httpx.Request("POST", "https://overpass.test")
        raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))

    monkeypatch.setattr(main.osm, "fetch_place_observations", rate_limited)
    client.get("/places/search", params={"q": "kawiarnia"})
    body = client.post("/places/osm-node-123/assessment", json={"preset": "step_free_strict", **TODAY}).json()
    assert "serwer ogranicza liczbę zapytań" in body["warnings"][0]
