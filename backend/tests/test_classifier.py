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
    assert kerb.primary_observation_id == "ai"  # ostrożniejszy wariant: przeszkoda wg zdjęcia
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
    assert result.text.splitlines()[1] == "Miejsce i wejście:"
    line = result.text.splitlines()[2]
    assert line.startswith("Krawężnik: może być przeszkodą, wymaga sprawdzenia")
    assert "automatyczna analiza zdjęcia z 12.06.2025" in line
    assert "2 źródła podają różne informacje" in line


def test_text_mentions_warnings_added_after_assess():
    result = assess([], WHEELCHAIR, TODAY)
    result.warnings.append("OSM niedostępne")
    assert "Ostrzeżenie: OSM niedostępne" in result.text
    assert "Ostrzeżenie: OSM niedostępne" in result.model_dump()["text"]


# --- miejsce vs okolica ---------------------------------------------------------

def test_stairs_around_do_not_make_place_inaccessible():
    """Kraków Główny: schody w okolicy dworca to nie 'nie do pokonania' dla samego miejsca."""
    obs = [
        make_obs("e", "entrance", {"width_cm": 120, "threshold_cm": 0}, source=SourceType.OWNER,
                 observed_at=TODAY, place_id="p"),
        make_obs("s1", "steps", {}, lat=50.0619),
        make_obs("s2", "steps", {}, lat=50.0621),
        make_obs("lift", "amenity", {"kind": "elevator"}, lat=50.0615),
    ]
    result = assess(group_observations(obs), WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE}, place_id="p")
    assert result.summary == Summary.NO_KNOWN_BARRIERS
    assert result.surroundings_counts[Verdict.BLOCKER] == 2
    assert "W okolicy: schody (przeszkoda) x2, winda. Sprawdź trasę dojścia." in result.summary_text
    assert [f.scope.value for f in result.features][0] == "place"  # najpierw miejsce


def test_blocker_at_place_still_decides():
    obs = [
        make_obs("e", "entrance", {"width_cm": 120, "threshold_cm": 15}, source=SourceType.OWNER,
                 observed_at=TODAY, place_id="p"),
    ]
    result = assess(group_observations(obs), WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE}, place_id="p")
    assert result.summary == Summary.BARRIERS


def test_only_surroundings_is_incomplete():
    obs = [make_obs("s", "surface", {"value": "asphalt"})]
    result = assess(group_observations(obs), WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE}, place_id="p")
    assert result.summary == Summary.INCOMPLETE_DATA


def test_without_place_id_everything_counts():
    """/classify bez miejsca - zachowanie jak dotąd."""
    obs = [make_obs("k", "kerb", {"kind": "raised", "height_cm": 15})]
    assert assess(group_observations(obs), WHEELCHAIR, TODAY).summary == Summary.BARRIERS


# --- pewność ------------------------------------------------------------------

def test_confidence_single_source_equals_trust():
    obs = [make_obs("k", "kerb", {"kind": "lowered"}, source=SourceType.OWNER, observed_at=TODAY)]
    [kerb] = assess(group_observations(obs), WHEELCHAIR, TODAY).features
    assert kerb.confidence_pct == 85


def test_agreeing_sources_raise_confidence():
    one = [make_obs("a", "kerb", {"kind": "lowered"}, source=SourceType.OSM, observed_at=TODAY)]
    two = one + [make_obs("b", "kerb", {"kind": "flush"}, source=SourceType.AI_DETECTION, observed_at=TODAY)]
    [single] = assess(group_observations(one), WHEELCHAIR, TODAY).features
    [double] = assess(group_observations(two), WHEELCHAIR, TODAY).features
    assert single.confidence_pct == 70
    assert double.confidence_pct == 82  # 1 - 0.3 * 0.6


def test_conflict_lowers_confidence():
    result = assess(group_observations(_conflicting_kerb()), WHEELCHAIR, TODAY)
    [kerb] = result.features
    assert kerb.confidence_pct < 20  # dwa słabe, sprzeczne źródła
    assert kerb.verdict == Verdict.UNCERTAIN  # słaba przeszkoda -> do sprawdzenia
    assert "pewność" in kerb.reasons[-1]


def test_weak_old_photo_does_not_make_place_inaccessible():
    """Sukiennice: jedyne konkretne źródło o progu to zdjęcie z 2016 r. (wiarygodność ~3%)."""
    obs = [make_obs("old", "entrance", {"width_cm": 140, "threshold_cm": {"lo": 5, "hi": 10}},
                    source=SourceType.AI_DETECTION, observed_at=date(2016, 8, 27), confidence=0.8, place_id="p")]
    result = assess(group_observations(obs), WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE}, place_id="p")
    assert result.features[0].verdict == Verdict.UNCERTAIN
    assert result.summary == Summary.DIFFICULTIES


def test_strong_blocker_stays_blocker():
    obs = [make_obs("k", "kerb", {"kind": "raised", "height_cm": 15}, source=SourceType.OWNER, observed_at=TODAY)]
    [kerb] = assess(group_observations(obs), WHEELCHAIR, TODAY).features
    assert (kerb.verdict, kerb.confidence_pct) == (Verdict.BLOCKER, 85)


def test_uncertain_capped_at_50_and_unknown_has_none():
    stroller = needs_from_preset("stroller")
    obs = [make_obs("k", "kerb", {"kind": "raised", "height_cm": {"lo": 8, "hi": 15}}, source=SourceType.OWNER,
                    observed_at=TODAY)]
    [kerb] = assess(group_observations(obs), stroller, TODAY).features
    assert kerb.verdict == Verdict.UNCERTAIN and kerb.confidence_pct == 50
    [unknown] = assess(group_observations([make_obs("s", "surface", {})]), WHEELCHAIR, TODAY).features
    assert unknown.confidence_pct is None


def test_summary_confidence_and_text():
    obs = [make_obs("e", "entrance", {"width_cm": 120, "threshold_cm": 0}, source=SourceType.OWNER,
                    observed_at=TODAY, place_id="p")]
    result = assess(group_observations(obs), WHEELCHAIR, TODAY, required={FeatureType.ENTRANCE}, place_id="p")
    assert result.summary_confidence_pct == 85
    assert "Pewność oceny: 85%." in result.text
    assert "Pewność: 85%." in result.text
