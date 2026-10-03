from datetime import date, timedelta

from conftest import TODAY, make_obs

from bezbarier.classification import DataStatus, SourceType
from bezbarier.classification.trust import data_status, trust_score
from bezbarier.storage import InMemoryRepository, Vote, VoteValue


def _repo_with(obs):
    repo = InMemoryRepository()
    repo.add_observations([obs])
    return repo


def _vote(repo, voter, value, when=TODAY):
    return repo.add_vote(Vote(observation_id="ai", voter_id=voter, value=value, created_at=when))


def _ai_obs(observed_at=TODAY):
    return make_obs("ai", "kerb", {"kind": "lowered"}, source=SourceType.AI_DETECTION, observed_at=observed_at)


def test_three_confirmations_verify_ai_detection():
    repo = _repo_with(_ai_obs())
    before = trust_score(repo.get_observation("ai"), TODAY)
    for voter in ("a", "b", "c"):
        obs = _vote(repo, voter, VoteValue.CONFIRM)
    assert obs.confirmations == 3
    assert data_status(obs, TODAY) == DataStatus.CONFIRMED
    assert trust_score(obs, TODAY) > before


def test_denials_lower_trust():
    repo = _repo_with(_ai_obs())
    before = trust_score(repo.get_observation("ai"), TODAY)
    obs = _vote(repo, "a", VoteValue.DENY)
    assert obs.denials == 1
    assert trust_score(obs, TODAY) == before / 2


def test_one_vote_per_voter():
    repo = _repo_with(_ai_obs())
    _vote(repo, "a", VoteValue.CONFIRM)
    obs = _vote(repo, "a", VoteValue.DENY)  # zmiana zdania nadpisuje
    assert (obs.confirmations, obs.denials) == (0, 1)


def test_confirmation_refreshes_old_data():
    old = TODAY - timedelta(days=1500)
    repo = _repo_with(_ai_obs(observed_at=old))
    assert data_status(repo.get_observation("ai"), TODAY) == DataStatus.OUTDATED
    obs = _vote(repo, "a", VoteValue.CONFIRM)
    assert obs.last_confirmed_at == TODAY
    assert data_status(obs, TODAY) == DataStatus.UNVERIFIED  # świeże, ale jeszcze nie zweryfikowane


def test_correction_creates_user_report():
    repo = _repo_with(_ai_obs())
    original = repo.get_observation("ai")
    correction = repo.add_correction(original, {"kind": "raised", "height_cm": 12}, date(2026, 10, 3))
    assert correction.source.type == SourceType.USER_REPORT
    assert correction.type == original.type
    assert repo.get_observation(correction.id) is not None
