from datetime import date

from conftest import TODAY, make_obs

from bezbarier.classification import GeoPoint, SourceType
from bezbarier.storage import Place, SqliteRepository, Vote, VoteValue


def test_data_survives_restart(tmp_path):
    db = tmp_path / "test.db"
    repo = SqliteRepository(db)
    repo.add_place(Place(id="osm-way-1", name="Sukiennice", location=GeoPoint(lat=50.0617, lon=19.9373), data_loaded=False))
    repo.add_observations([make_obs("ai", "surface", {"value": "sett"}, source=SourceType.AI_DETECTION, place_id="osm-way-1")])
    repo.add_vote(Vote(observation_id="ai", voter_id="d1", value=VoteValue.CONFIRM, created_at=TODAY))
    correction = repo.add_correction(repo.get_observation("ai"), {"value": "paving_stones"}, TODAY)
    repo.mark_loaded("osm-way-1")
    repo.analyzed_images.add("mapillary-123")
    repo.save_analyzed_images()
    repo.close()

    reopened = SqliteRepository(db)
    assert reopened.places["osm-way-1"].data_loaded is True
    obs = reopened.get_observation("ai")
    assert obs.attrs == {"value": "sett"}
    assert (obs.confirmations, obs.last_confirmed_at) == (1, TODAY)
    assert reopened.get_observation(correction.id).source.type == SourceType.USER_REPORT
    assert "mapillary-123" in reopened.analyzed_images
    assert {o.id for o in reopened.observations_for_place("osm-way-1")} == {"ai", correction.id}
    reopened.close()


def test_vote_overwrite_persisted(tmp_path):
    db = tmp_path / "test.db"
    repo = SqliteRepository(db)
    repo.add_observations([make_obs("o", "kerb", {})])
    repo.add_vote(Vote(observation_id="o", voter_id="d1", value=VoteValue.CONFIRM, created_at=date(2026, 1, 1)))
    repo.add_vote(Vote(observation_id="o", voter_id="d1", value=VoteValue.DENY, created_at=date(2026, 2, 1)))
    repo.close()

    obs = SqliteRepository(db).get_observation("o")
    assert (obs.confirmations, obs.denials) == (0, 1)


def test_sample_file_reload_is_idempotent(tmp_path):
    from bezbarier.api.main import SAMPLE_DATA

    db = tmp_path / "test.db"
    repo = SqliteRepository(db)
    repo.load_file(SAMPLE_DATA)
    count = len(repo.places)
    repo.close()
    repo = SqliteRepository(db)
    repo.load_file(SAMPLE_DATA)
    assert len(repo.places) == count
    repo.close()
