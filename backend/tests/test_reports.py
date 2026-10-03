import pytest
from fastapi.testclient import TestClient

from bezbarier.api import main, security
from bezbarier.classification import FeatureType
from bezbarier.classification.report_schema import validate_report
from bezbarier.storage import InMemoryRepository, SqliteRepository

client = TestClient(main.app)
TODAY = {"today": "2026-10-03"}
PRESET = {"preset": "step_free_strict", **TODAY}


@pytest.fixture(autouse=True)
def fresh_repo(monkeypatch):
    monkeypatch.setattr(main, "repo", InMemoryRepository.from_file(main.SAMPLE_DATA))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(security, "_vote_limiter", None)


# --- walidacja -----------------------------------------------------------------

def test_valid_report_cleaned():
    assert validate_report(FeatureType.ENTRANCE, {"width_cm": 90, "threshold_cm": 0, "automatic_door": None}) == {
        "width_cm": 90.0, "threshold_cm": 0.0,
    }


@pytest.mark.parametrize(
    ("feature_type", "attrs", "message"),
    [
        (FeatureType.ENTRANCE, {"width_cm": 9999}, "zakresie"),
        (FeatureType.ENTRANCE, {"color": "red"}, "nieznane pola"),
        (FeatureType.ENTRANCE, {}, "co najmniej jedną"),
        (FeatureType.KERB, {"kind": "very high"}, "jedną z wartości"),
        (FeatureType.STEPS, {"count": 2.5}, "całkowitą"),
        (FeatureType.STEPS, {"ramp": "yes"}, "tak/nie"),
        (FeatureType.AMENITY, {}, "co najmniej jedną"),
    ],
)
def test_invalid_reports_rejected(feature_type, attrs, message):
    with pytest.raises(ValueError, match=message):
        validate_report(feature_type, attrs)


# --- zgłoszenia użytkowników -----------------------------------------------------------

def test_user_report_fills_missing_entrance_data():
    before = client.post("/places/kawiarnia-rynek/assessment", json=PRESET).json()
    assert "próg w wejściu" in before["missing"]

    resp = client.post("/places/kawiarnia-rynek/reports", json={"type": "entrance", "attrs": {"threshold_cm": 0}})
    assert resp.status_code == 200
    assert resp.json()["source"]["type"] == "user_report"
    assert resp.json()["place_id"] == "kawiarnia-rynek"

    after = client.post("/places/kawiarnia-rynek/assessment", json=PRESET).json()
    entrance = next(f for f in after["features"] if f["type"] == "entrance")
    assert len(entrance["evidence"]) == 2  # właściciel (szerokość) + użytkownik (próg) w jednej cesze


def test_report_validation_error_is_400():
    resp = client.post("/places/kawiarnia-rynek/reports", json={"type": "entrance", "attrs": {"width_cm": -5}})
    assert resp.status_code == 400
    assert "width_cm" in resp.json()["detail"]


def test_correction_validated():
    resp = client.post("/observations/mly-1001-0/votes", json={
        "voter_id": "d", "value": "deny", "correction_attrs": {"kind": "bardzo wysoki"},
    })
    assert resp.status_code == 400


def test_reports_rate_limited(monkeypatch):
    monkeypatch.setenv("VOTE_RATE_LIMIT_PER_HOUR", "1")
    body = {"type": "amenity", "attrs": {"kind": "bench"}}
    assert client.post("/places/kawiarnia-rynek/reports", json=body).status_code == 200
    assert client.post("/places/kawiarnia-rynek/reports", json=body).status_code == 429


# --- właściciel obiektu ---------------------------------------------------------

def _owner_code(place_id="kawiarnia-rynek"):
    resp = client.post("/admin/owner-codes", json={"place_id": place_id, "owner_name": "Kawiarnia Testowa sp. z o.o."})
    assert resp.status_code == 200
    return resp.json()


def test_owner_code_and_report_give_confirmed_data():
    issued = _owner_code()
    assert issued["form_path"] == "/app/#/wlasciciel/kawiarnia-rynek"
    resp = client.post(
        "/places/kawiarnia-rynek/owner-reports",
        headers={"X-Owner-Code": issued["code"].lower()},  # wielkość liter nie ma znaczenia
        json={"reports": [
            {"type": "entrance", "attrs": {"width_cm": 100, "threshold_cm": 0}},
            {"type": "amenity", "attrs": {"kind": "toilets_wheelchair"}},
        ]},
    )
    assert resp.status_code == 200
    assert [o["source"]["name"] for o in resp.json()] == ["Właściciel: Kawiarnia Testowa sp. z o.o."] * 2

    after = client.post("/places/kawiarnia-rynek/assessment", json=PRESET).json()
    entrance = next(f for f in after["features"] if f["type"] == "entrance")
    assert entrance["verdict"] == "ok"
    assert entrance["status"] == "confirmed"
    assert "próg w wejściu" not in after["missing"]


def test_owner_code_bound_to_place():
    issued = _owner_code("kawiarnia-rynek")
    body = {"reports": [{"type": "entrance", "attrs": {"width_cm": 100}}]}
    assert client.post("/places/muzeum-kazimierz/owner-reports", headers={"X-Owner-Code": issued["code"]},
                       json=body).status_code == 401
    assert client.post("/places/kawiarnia-rynek/owner-reports", headers={"X-Owner-Code": "ZLY-KOD"},
                       json=body).status_code == 401
    assert client.post("/places/kawiarnia-rynek/owner-reports", json=body).status_code == 401


def test_owner_report_all_or_nothing():
    issued = _owner_code()
    resp = client.post("/places/kawiarnia-rynek/owner-reports", headers={"X-Owner-Code": issued["code"]}, json={
        "reports": [{"type": "entrance", "attrs": {"width_cm": 100}}, {"type": "entrance", "attrs": {"width_cm": 1}}],
    })
    assert resp.status_code == 400
    sources = [e["source"]["type"] for f in client.post("/places/kawiarnia-rynek/assessment", json=PRESET).json()["features"]
               for e in f["evidence"]]
    assert sources.count("owner") == 1  # tylko właściciel z danych przykładowych - nic nie zostało dodane


def test_owner_codes_admin_only(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "sekret")
    resp = client.post("/admin/owner-codes", json={"place_id": "kawiarnia-rynek", "owner_name": "X sp."})
    assert resp.status_code == 401


def test_owner_code_persisted_as_hash(tmp_path):
    db = tmp_path / "t.db"
    repo = SqliteRepository(db)
    code = repo.create_owner_code("p", "Hotel")
    repo.close()
    raw = db.read_bytes()
    assert code.encode() not in raw
    assert SqliteRepository(db).owner_for_code("p", code) == "Hotel"
