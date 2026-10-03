import os
from datetime import date

import pytest

from bezbarier.classification import GeoPoint, Observation, Source, SourceType

# Przed importem API: testy nie tworzą pliku bazy (load_dotenv nie nadpisuje już ustawionych zmiennych)
os.environ["DATABASE_PATH"] = ":memory:"

TODAY = date(2026, 10, 3)


@pytest.fixture
def today() -> date:
    return TODAY


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
