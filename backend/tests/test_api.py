import httpx
import pytest
from conftest import make_obs
from fastapi.testclient import TestClient

from bezbarier.api import main
from bezbarier.storage import InMemoryRepository

client = TestClient(main.app)

TODAY = {"today": "2026-10-03"}


@pytest.fixture(autouse=True)
def fresh_repo(monkeypatch):
    monkeypatch.setattr(main, "repo", InMemoryRepository.from_file(main.SAMPLE_DATA))


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
    assert body["summary"] == "barriers"
    assert body["contains_sample_data"] is True
    kerb = next(f for f in body["features"] if f["type"] == "kerb")
    assert kerb["conflict"] is True


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
