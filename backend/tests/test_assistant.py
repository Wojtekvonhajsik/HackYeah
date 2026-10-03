from types import SimpleNamespace

import pytest
from conftest import TODAY, make_obs
from fastapi.testclient import TestClient

from bezbarier.api import main, security
from bezbarier.assistant import Candidate, answer_question, question_categories
from bezbarier.classification import GeoPoint, SourceType
from bezbarier.storage import InMemoryRepository, Place, Sponsorship

client = TestClient(main.app)
BODY = {"preset": "step_free_strict", "today": "2026-10-03"}


def place(pid, name, kind, lat):
    return Place(id=pid, name=name, kind=kind, location=GeoPoint(lat=lat, lon=19.9373))


@pytest.fixture(autouse=True)
def repo(monkeypatch):
    r = InMemoryRepository()
    r.add_place(place("cafe-ok", "Kawiarnia Płaska", "amenity:cafe", 50.0617))
    r.add_place(place("cafe-steps", "Kawiarnia na Piętrze", "amenity:cafe", 50.0640))
    r.add_place(place("museum", "Muzeum Testowe", "tourism:museum", 50.0660))
    r.add_observations([
        make_obs("e1", "entrance", {"width_cm": 100, "threshold_cm": 0}, source=SourceType.OWNER, observed_at=TODAY,
                 lat=50.0617, place_id="cafe-ok"),
        make_obs("e2", "entrance", {"width_cm": 100, "threshold_cm": 0}, source=SourceType.OWNER, observed_at=TODAY,
                 lat=50.0640, place_id="cafe-steps"),
        make_obs("s2", "steps", {"count": 2}, source=SourceType.OWNER, observed_at=TODAY, lat=50.0640,
                 place_id="cafe-steps"),
        make_obs("e3", "entrance", {"width_cm": 100}, lat=50.0660, place_id="museum"),
    ])
    for p in list(r.places.values()):
        r.mark_loaded(p.id, TODAY)
    monkeypatch.setattr(main, "repo", r)
    monkeypatch.setattr(security, "_assistant_limiter", None)
    monkeypatch.setattr(security, "_vote_limiter", None)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    return r


def ask(question, **extra):
    return client.post("/assistant", json={**BODY, "question": question, **extra}).json()


def sponsor(pid, tagline="Zapraszamy"):
    return client.post("/admin/sponsorships", json={"place_id": pid, "sponsor_name": f"Firma {pid}", "tagline": tagline})


def test_categories():
    assert question_categories("Gdzie zjem obiad bez schodów?") == ["jedzenie"]
    assert question_categories("Jakie muzeum polecasz?") == ["kultura"]


def test_rules_answer_ranks_by_assessment_only():
    body = ask("Gdzie zjem coś bez schodów?")
    assert body["engine"] == "reguły"
    assert [p["place_id"] for p in body["places"]] == ["cafe-ok", "cafe-steps"]  # muzeum nie pasuje do pytania
    assert body["places"][0]["summary"] == "no_known_barriers"
    assert "dostępn" not in body["answer"].lower()
    assert body["sponsored"] is None


def test_sponsorship_requires_owner_data(repo):
    repo.add_place(place("no-owner", "Bez właściciela", "amenity:cafe", 50.07))
    resp = sponsor("no-owner")
    assert resp.status_code == 400
    assert "właściciela" in resp.json()["detail"]


def test_ad_not_shown_to_people_it_has_barriers_for():
    assert sponsor("cafe-steps").status_code == 200
    # kawiarnia ze schodami ma przeszkody dla profilu "bez stopni" - nie reklamujemy jej tej osobie
    assert ask("Gdzie na kawę?")["sponsored"] is None


def test_paid_place_does_not_jump_ahead():
    assert sponsor("cafe-steps", "Kawa -10%").status_code == 200
    # dla wózka dziecięcego 2 stopnie to utrudnienie (nie blokada), więc reklama byłaby dozwolona,
    # ale kolejność poleceń zależy tylko od oceny: płaska kawiarnia zostaje pierwsza
    body = ask("Gdzie na kawę?", preset="stroller")
    assert [p["place_id"] for p in body["places"]] == ["cafe-ok", "cafe-steps"]
    assert all(not p["sponsored"] for p in body["places"])
    assert body["sponsored"] is None  # już polecona na podstawie danych - bez dublowania jako reklama


def test_ad_not_duplicated_when_recommended_anyway():
    assert sponsor("cafe-ok").status_code == 200
    body = ask("kawiarnia")
    assert body["places"][0]["place_id"] == "cafe-ok"  # najlepsza ocena - polecona na podstawie danych
    assert body["sponsored"] is None


def test_sponsored_slot_impressions_and_clicks():
    sp = sponsor("cafe-ok").json()
    candidates = [
        Candidate(place_id="m1", name="Muzeum A", kind="tourism:museum", summary="no_known_barriers",
                  summary_confidence_pct=90, missing=[], highlights=[]),
        Candidate(place_id="m2", name="Muzeum B", kind="tourism:museum", summary="no_known_barriers",
                  summary_confidence_pct=80, missing=[], highlights=[]),
        Candidate(place_id="m3", name="Muzeum C", kind="tourism:museum", summary="no_known_barriers",
                  summary_confidence_pct=70, missing=[], highlights=[]),
        Candidate(place_id="cafe-ok", name="Kawiarnia Płaska", kind="amenity:cafe", summary="no_known_barriers",
                  summary_confidence_pct=60, missing=[], highlights=[], sponsorship=Sponsorship.model_validate(sp)),
    ]
    assert answer_question("co zwiedzić?", candidates, "Bez stopni", use_llm=False).sponsored is None  # nie pasuje
    result = answer_question("gdzie warto pójść", candidates, "Bez stopni", use_llm=False)
    assert [p.place_id for p in result.places] == ["m1", "m2", "m3"]  # reklama nie zmienia kolejności
    assert result.sponsored.place_id == "cafe-ok" and result.sponsored.sponsored

    assert client.post(f"/sponsorships/{sp['id']}/click").status_code == 200
    assert client.get("/admin/sponsorships").json()[0]["clicks"] == 1
    assert client.post("/sponsorships/nie-ma/click").status_code == 404


def test_llm_used_when_available_and_cannot_invent_places():
    pytest.importorskip("google.genai")
    candidates = [Candidate(place_id="cafe-ok", name="Kawiarnia Płaska", kind="amenity:cafe",
                            summary="no_known_barriers", summary_confidence_pct=85, missing=[], highlights=[])]
    sent = {}
    output = '{"answer": "Polecam Kawiarnię Płaską.", "place_ids": ["cafe-ok", "zmyslone-miejsce"]}'

    def create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(output_text=output)

    fake = SimpleNamespace(interactions=SimpleNamespace(create=create))
    result = answer_question("gdzie na kawę", candidates, "Bez stopni", use_llm=True, llm_client=fake)
    assert result.engine.startswith("gemini")
    assert [p.place_id for p in result.places] == ["cafe-ok"]  # zmyślone id odrzucone
    assert sent["store"] is False
    assert "sponsor" not in sent["input"][0]["text"].lower()  # model nie widzi reklam


def test_llm_failure_falls_back_to_rules():
    pytest.importorskip("google.genai")

    def broken(**kwargs):
        raise RuntimeError("limit")

    fake = SimpleNamespace(interactions=SimpleNamespace(create=broken))
    candidates = [Candidate(place_id="m", name="Muzeum", kind="tourism:museum", summary="incomplete_data",
                            summary_confidence_pct=None, missing=[], highlights=[])]
    result = answer_question("muzeum", candidates, "Bez stopni", use_llm=True, llm_client=fake)
    assert result.engine == "reguły (model AI niedostępny)"
    assert result.places[0].place_id == "m"


def test_assistant_rate_limited(monkeypatch):
    monkeypatch.setenv("ASSISTANT_RATE_LIMIT_PER_HOUR", "1")
    assert client.post("/assistant", json={**BODY, "question": "kawa"}).status_code == 200
    resp = client.post("/assistant", json={**BODY, "question": "kawa"})
    assert resp.status_code == 429
    assert "pytań do asystenta" in resp.json()["detail"]
