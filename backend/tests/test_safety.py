from datetime import date

from conftest import make_obs
from fastapi.testclient import TestClient

from bezbarier.api import main
from bezbarier.classification import needs_from_preset
from bezbarier.safety import assess_safety, crossing_info
from bezbarier.sources import gus
from bezbarier.storage import InMemoryRepository

VALUES = {
    "city": {"accidents": {2025: 942}, "injured": {2025: 1026}, "killed": {2025: 6},
             "accidents_per_100k": {2025: 116.2}, "killed_per_100k": {2025: 0.74}, "crimes_per_1000": {2025: 22.95}},
    "country": {"accidents_per_100k": {2025: 55.9}, "killed_per_100k": {2025: 4.44}, "crimes_per_1000": {2025: 20.92}},
}
CITY = gus.build_safety(VALUES, gus.KRAKOW_UNIT_ID, date(2026, 10, 3))


def test_city_safety_ratios():
    by_key = {i.key: i for i in CITY.indicators}
    assert by_key["accidents_per_100k"].ratio == 2.08
    assert by_key["killed_per_100k"].ratio == 0.17
    assert (CITY.accidents, CITY.injured, CITY.killed, CITY.year) == (942, 1026, 6, 2025)


def test_crossings_counted_once_per_place():
    obs = [
        make_obs("a", "crossing", {"traffic_signals": True, "sound_signals": True}, lat=50.0617),
        make_obs("b", "crossing", {"traffic_signals": True}, lat=50.06171),   # to samo przejście z innego źródła
        make_obs("c", "crossing", {"traffic_signals": False}, lat=50.0620),
    ]
    info = crossing_info(obs)
    assert (info.total, info.with_signals, info.with_sound) == (2, 1, 1)


def test_elevated_risk_headline_and_tips_for_blind():
    obs = [make_obs("c", "crossing", {"traffic_signals": True, "sound_signals": False})]
    safety = assess_safety(CITY, obs, needs_from_preset("non_visual"))
    assert safety.level == "elevated"
    assert "2,1 raza więcej" in safety.headline
    assert any("Wypadki drogowe: 116 na 100 tys. mieszkańców (Polska: 56)" in f for f in safety.facts)
    assert "dźwiękową" in safety.tips[0]


def test_tips_for_wheelchair_and_missing_gus():
    safety = assess_safety(CITY, [], needs_from_preset("step_free_strict"))
    assert "obniżonym krawężnikiem" in safety.tips[0]
    assert "Brak danych o przejściach" in safety.facts[-1]
    no_data = assess_safety(None, [], needs_from_preset("step_free_strict"))
    assert no_data.level == "unknown"


def test_assessment_starts_with_safety(monkeypatch):
    monkeypatch.setattr(main, "repo", InMemoryRepository.from_file(main.SAMPLE_DATA))
    client = TestClient(main.app)
    body = client.post("/places/kawiarnia-rynek/assessment", json={"preset": "non_visual", "today": "2026-10-03"}).json()
    assert body["safety"]["level"] == "elevated"  # z zapisanej kopii GUS (testy bez sieci)
    assert body["safety"]["city"]["from_snapshot"] is True
    assert body["text"].startswith("Bezpieczeństwo: Wypadków drogowych jest tu 2,1 raza więcej")
