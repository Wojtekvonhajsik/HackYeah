from concurrent.futures import Executor, Future
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from bezbarier.api import main
from bezbarier.sources import gus
from bezbarier.storage import InMemoryRepository

client = TestClient(main.app)
NOMINATIM = [{
    "osm_type": "way", "osm_id": 26195267, "lat": "50.07", "lon": "19.95", "category": "amenity",
    "type": "place_of_worship", "name": "Bazylika Mariacka", "display_name": "Bazylika Mariacka, Kraków",
}]


class NeverFinishes(Executor):
    """Pobieranie z OSM "trwa" - ocena nie może na nie czekać."""

    def __init__(self):
        self.submitted = 0

    def submit(self, fn, /, *args, **kwargs):
        self.submitted += 1
        return Future()


@pytest.fixture(autouse=True)
def fresh_repo(monkeypatch):
    monkeypatch.setattr(main, "repo", InMemoryRepository.from_file(main.SAMPLE_DATA))
    monkeypatch.setattr(main.osm, "search_places", lambda q: NOMINATIM)


def test_assessment_returns_immediately_while_osm_loads(monkeypatch):
    executor = NeverFinishes()
    monkeypatch.setattr(main, "osm_executor", executor)
    found = client.get("/places/search", params={"q": "mariacki"}).json()
    assert found[0]["kind"] == "amenity:place_of_worship"
    assert executor.submitted == 1  # pobieranie ruszyło już przy wyszukiwaniu

    body = client.post("/places/osm-way-26195267/assessment", json={"preset": "step_free_strict"}).json()
    assert body["pending_sources"] == ["OpenStreetMap"]
    assert body["summary"] == "incomplete_data"
    assert "Pobieramy dane z OpenStreetMap" in body["warnings"][0]
    client.post("/places/osm-way-26195267/assessment", json={"preset": "step_free_strict"})
    assert executor.submitted == 1  # kolejne oceny nie uruchamiają pobierania od nowa


def test_failed_osm_not_retried_during_cooldown(monkeypatch):
    calls = []

    def broken(*args):
        calls.append(args)
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(main.osm, "fetch_place_observations", broken)
    client.get("/places/search", params={"q": "mariacki"})
    for _ in range(3):  # np. głosowanie przelicza ocenę - nie może za każdym razem czekać na OSM
        body = client.post("/places/osm-way-26195267/assessment", json={"preset": "step_free_strict"}).json()
        assert "serwer nie odpowiedział na czas" in body["warnings"][0]
        assert body["pending_sources"] == []
    assert len(calls) == 1


def test_wait_param_waits_for_osm(monkeypatch):
    monkeypatch.setattr(main.osm, "fetch_place_observations", lambda *a: [])
    monkeypatch.setattr(main, "PREFETCH_RESULTS", 0)
    client.get("/places/search", params={"q": "mariacki"})
    body = client.post("/places/osm-way-26195267/assessment", params={"wait": "true"},
                       json={"preset": "step_free_strict"}).json()
    assert body["pending_sources"] == []


# --- GUS ------------------------------------------------------------------------

VALUES = {
    "population": {2021: 800000, 2025: 816614},
    "post_working_age": {2025: 183315},
    "disabled": {2021: 110482},
    "disabled_legal": {2021: 72241},
    "lodging_total": {2025: 324},
    "lodging_elevator": {2025: 134},
    "lodging_ramp": {2025: 96},
    "lodging_auto_door": {2025: 88},
    "lodging_parking": {2025: 92},
}


def test_gus_stats_shares():
    stats = gus.stats_summary(gus.build_stats(VALUES, gus.KRAKOW_UNIT_ID, date(2026, 10, 3)))
    assert stats["disabled"].share_pct == pytest.approx(13.8)  # udział liczony z ludności z tego samego roku
    assert stats["post_working_age"].share_pct == pytest.approx(22.4)
    assert stats["lodging_ramp"].share_pct == pytest.approx(29.6)
    assert stats["lodging_ramp"].share_of == "obiektów noclegowych"


def test_gus_endpoint_falls_back_to_snapshot(monkeypatch):
    def down(unit_id):
        raise httpx.ConnectError("GUS niedostępny")

    monkeypatch.setattr(gus, "fetch_values", down)
    monkeypatch.setattr(gus, "_cache", {})
    body = client.get("/stats/city").json()
    assert body["from_snapshot"] is True
    assert body["license"] == "CC BY 4.0"
    assert any(item["key"] == "disabled" for item in body["items"])


def test_gus_endpoint_503_without_snapshot(monkeypatch, tmp_path):
    def down(unit_id):
        raise httpx.ConnectError("GUS niedostępny")

    monkeypatch.setattr(gus, "fetch_values", down)
    monkeypatch.setattr(gus, "_cache", {})
    monkeypatch.setattr(gus, "SNAPSHOT_DIR", tmp_path)
    assert client.get("/stats/city").status_code == 503
