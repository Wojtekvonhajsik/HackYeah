from fastapi.testclient import TestClient

from bezbarier.api.main import app

client = TestClient(app)


def test_presets():
    resp = client.get("/presets")
    assert resp.status_code == 200
    assert "wheelchair_manual" in resp.json()


def test_sample_place_with_conflict():
    resp = client.post("/places/kawiarnia-rynek/assessment", json={"preset": "wheelchair_manual", "today": "2026-10-03"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "barriers"
    assert body["contains_sample_data"] is True
    kerb = next(f for f in body["features"] if f["type"] == "kerb")
    assert kerb["conflict"] is True


def test_sample_place_without_entrance_data():
    resp = client.post("/places/muzeum-kazimierz/assessment", json={"preset": "wheelchair_manual", "today": "2026-10-03"})
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
