import os
from concurrent.futures import Executor, Future
from datetime import date

import pytest

from bezbarier.classification import GeoPoint, Observation, Source, SourceType

# Przed importem API: testy nie tworzą pliku bazy (load_dotenv nie nadpisuje już ustawionych zmiennych)
os.environ["DATABASE_PATH"] = ":memory:"

TODAY = date(2026, 10, 3)


@pytest.fixture
def today() -> date:
    return TODAY


class SyncExecutor(Executor):
    """Zadania "w tle" wykonują się od razu - testy są powtarzalne."""

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as e:  # noqa: BLE001 - przekazujemy dalej jak prawdziwy executor
            future.set_exception(e)
        return future


@pytest.fixture(autouse=True)
def sync_osm_loading(monkeypatch):
    from bezbarier.api import main

    monkeypatch.setattr(main, "osm_executor", SyncExecutor())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # asystent w testach na regułach, bez płatnego modelu
    monkeypatch.setattr(main, "_osm_jobs", {})
    monkeypatch.setattr(main, "_osm_failed_at", {})


@pytest.fixture(autouse=True)
def gus_offline(monkeypatch):
    """Testy nie pytają GUS przez sieć - używają zapisanej kopii z data/."""
    import httpx

    from bezbarier.sources import gus

    def offline(*args, **kwargs):
        raise httpx.ConnectError("testy bez sieci")

    monkeypatch.setattr(gus, "fetch_values", offline)
    monkeypatch.setattr(gus, "fetch_safety_values", offline)
    monkeypatch.setattr(gus, "_cache", {})
    monkeypatch.setattr(gus, "_safety_cache", {})


@pytest.fixture(autouse=True)
def mock_detector(monkeypatch):
    """Testy nigdy nie wołają płatnych API, nawet jeśli w backend/.env jest DETECTOR=gemini."""
    monkeypatch.setenv("DETECTOR", "mock")


def make_obs(
    obs_id: str,
    type_: str,
    attrs: dict,
    source: SourceType = SourceType.OSM,
    observed_at: date = date(2026, 1, 1),
    confidence: float = 1.0,
    lat: float = 50.0617,
    lon: float = 19.9373,
    place_id: str | None = None,
) -> Observation:
    return Observation(
        id=obs_id,
        type=type_,
        attrs=attrs,
        location=GeoPoint(lat=lat, lon=lon),
        source=Source(type=source, name=f"test {source.value}", observed_at=observed_at),
        confidence=confidence,
        place_id=place_id,
    )
