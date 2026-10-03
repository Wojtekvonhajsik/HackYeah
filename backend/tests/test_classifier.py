from datetime import date

from conftest import TODAY, make_obs

from bezbarier.classification import (
    DataStatus,
    FeatureType,
    SourceType,
    Summary,
    Verdict,
    assess,
    group_observations,
    needs_from_preset,
)

WHEELCHAIR = needs_from_preset("step_free_strict")


def _conflicting_kerb():
    return [
        make_obs("osm", "kerb", {"kind": "lowered"}, source=SourceType.OSM, observed_at=date(2023, 5, 10), lat=50.06165),
        make_obs(
            "ai", "kerb", {"kind": "raised", "height_cm": {"lo": 8, "hi": 15}},
            source=SourceType.AI_DETECTION, observed_at=date(2025, 6, 12), confidence=0.82, lat=50.06166,
        ),
    ]


def test_conflict_shows_cautious_verdict_and_all_evidence():
    result = assess(group_observations(_conflicting_kerb()), WHEELCHAIR, TODAY)
    [kerb] = result.features
    assert kerb.conflict
    assert kerb.status == DataStatus.CONFLICTING
    assert kerb.verdict == Verdict.BLOCKER
    assert {e.observation_id for e in kerb.evidence} == {"osm", "ai"}


def test_much_more_trusted_source_wins_without_conflict():
    obs = _conflicting_kerb()
    obs[0] = make_obs("official", "kerb", {"kind": "lowered"}, source=SourceType.OFFICIAL, observed_at=TODAY, lat=50.06165)
    [kerb] = assess(group_observations(obs), WHEELCHAIR, TODAY).features
    assert not kerb.conflict
    assert kerb.verdict == Verdict.OK
    assert kerb.status == DataStatus.CONFIRMED


def test_no_data_is_incomplete_not_accessible():
    result = assess([], WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE})
    assert result.summary == Summary.INCOMPLETE_DATA
    assert "wejście" in result.missing


def test_unknown_feature_prevents_no_known_barriers():
    obs = [make_obs("e", "entrance", {"width_cm": 100}, place_id="p")]  # próg nieznany
    result = assess(group_observations(obs), WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE})
    assert result.summary == Summary.INCOMPLETE_DATA
    assert result.missing == ["próg w wejściu"]  # bez ogólnego "wejście" - wiemy, czego konkretnie brakuje


def test_complete_good_data_gives_no_known_barriers():
    obs = [make_obs("e", "entrance", {"width_cm": 100, "threshold_cm": 0}, source=SourceType.OWNER, observed_at=TODAY, place_id="p")]
    result = assess(group_observations(obs), WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE})
    assert result.summary == Summary.NO_KNOWN_BARRIERS
    assert "nie jest gwarancja" in result.summary_text


def test_no_data_at_all_is_incomplete_for_every_profile():
    for preset in ("non_visual", "stroller", "step_free_strict"):
        result = assess([], needs_from_preset(preset), TODAY)
        assert result.summary == Summary.INCOMPLETE_DATA


def test_source_failure_prevents_no_known_barriers():
    obs = [make_obs("e", "entrance", {"width_cm": 100, "threshold_cm": 0}, source=SourceType.OWNER, observed_at=TODAY, place_id="p")]
    result = assess(group_observations(obs), WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE}, warnings=["OSM niedostępne"])
    assert result.summary == Summary.INCOMPLETE_DATA
    assert result.warnings == ["OSM niedostępne"]


def test_irrelevant_features_are_hidden():
    obs = [make_obs("s", "surface", {"value": "gravel"})]
    result = assess(group_observations(obs), needs_from_preset("non_visual"), TODAY)
    assert result.features == []


def test_features_sorted_worst_first():
    obs = [
        make_obs("s", "surface", {"value": "asphalt"}),
        make_obs("k", "kerb", {"kind": "raised", "height_cm": 15}, lat=50.0620),
    ]
    result = assess(group_observations(obs), WHEELCHAIR, TODAY)
    assert [f.verdict for f in result.features] == [Verdict.BLOCKER, Verdict.OK]
    assert result.summary == Summary.BARRIERS


def test_unverified_flag():
    obs = [make_obs("s", "surface", {"value": "asphalt"}, source=SourceType.AI_DETECTION, observed_at=TODAY)]
    assert assess(group_observations(obs), WHEELCHAIR, TODAY).contains_unverified


def test_text_names_source_of_verdict_not_most_trusted():
    result = assess(group_observations(_conflicting_kerb()), WHEELCHAIR, TODAY)
    [kerb] = result.features
    assert kerb.primary_observation_id == "ai"
    line = result.text.splitlines()[1]
    assert line.startswith("Krawężnik: przeszkoda nie do pokonania")
    assert "automatyczna analiza zdjęcia z 12.06.2025" in line
    assert "2 źródła podają różne informacje" in line


def test_text_mentions_warnings_added_after_assess():
    result = assess([], WHEELCHAIR, TODAY)
    result.warnings.append("OSM niedostępne")
    assert "Ostrzeżenie: OSM niedostępne" in result.text
    assert "Ostrzeżenie: OSM niedostępne" in result.model_dump()["text"]
