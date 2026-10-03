from datetime import date, timedelta

import pytest
from conftest import TODAY, make_obs

from bezbarier.classification import DataStatus, SourceType, group_observations
from bezbarier.classification.trust import data_status, freshness, trust_score


def test_freshness_halves_after_half_life():
    obs = make_obs("k", "kerb", {}, observed_at=TODAY - timedelta(days=730))
    assert freshness(obs, TODAY) == pytest.approx(0.5)


def test_temporary_obstacle_ages_fast():
    obs = make_obs("o", "obstacle", {"temporary": True}, observed_at=TODAY - timedelta(days=28))
    assert freshness(obs, TODAY) == pytest.approx(0.25)
    assert data_status(obs, TODAY) == DataStatus.OUTDATED


def test_ai_detection_less_trusted_than_owner():
    ai = make_obs("a", "entrance", {}, source=SourceType.AI_DETECTION, observed_at=TODAY, confidence=0.9)
    owner = make_obs("b", "entrance", {}, source=SourceType.OWNER, observed_at=TODAY)
    assert trust_score(ai, TODAY) < trust_score(owner, TODAY)


def test_status():
    assert data_status(make_obs("a", "kerb", {}, source=SourceType.OWNER, observed_at=TODAY), TODAY) == DataStatus.CONFIRMED
    assert data_status(make_obs("b", "kerb", {}, source=SourceType.AI_DETECTION, observed_at=TODAY), TODAY) == DataStatus.UNVERIFIED


def test_grouping_by_distance_and_type():
    obs = [
        make_obs("k1", "kerb", {}, lat=50.06165),
        make_obs("k2", "kerb", {}, lat=50.06166),  # ~1 m dalej -> ta sama cecha
        make_obs("k3", "kerb", {}, lat=50.06200),  # ~39 m dalej -> inna cecha
        make_obs("s1", "surface", {}, lat=50.06165),  # inny typ -> inna cecha
    ]
    features = group_observations(obs)
    assert sorted(len(f.observations) for f in features) == [1, 1, 2]


def test_entrance_grouped_by_place():
    obs = [
        make_obs("e1", "entrance", {}, lat=50.0617, place_id="p"),
        make_obs("e2", "entrance", {}, lat=50.0620, place_id="p"),
    ]
    assert len(group_observations(obs)) == 1


def test_amenities_of_different_kind_not_merged():
    obs = [
        make_obs("a1", "amenity", {"kind": "bench"}),
        make_obs("a2", "amenity", {"kind": "toilets_wheelchair"}),
    ]
    assert len(group_observations(obs)) == 2


def test_date_parsing_from_json():
    obs = make_obs("x", "kerb", {}, observed_at=date(2025, 6, 12))
    assert obs.source.observed_at.year == 2025


def test_surface_grouped_across_place_ids():
    obs = [
        make_obs("osm", "surface", {"value": "sett"}, lat=50.06165),
        make_obs("ai", "surface", {"value": "sett"}, lat=50.06166, place_id="p"),
    ]
    assert len(group_observations(obs)) == 1


def test_entrances_of_different_places_not_merged():
    obs = [
        make_obs("e1", "entrance", {}, place_id="a"),
        make_obs("e2", "entrance", {}, place_id="b"),
    ]
    assert len(group_observations(obs)) == 2
