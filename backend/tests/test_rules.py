import pytest

from bezbarier.classification import FeatureType, Range, Verdict, needs_from_preset
from bezbarier.classification.needs import MaxThreshold, MinThreshold
from bezbarier.classification.rules import check_max, check_min, evaluate

WHEELCHAIR = needs_from_preset("wheelchair_manual")
STROLLER = needs_from_preset("stroller")
BLIND = needs_from_preset("blind")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, Verdict.UNKNOWN),
        (Range(lo=0, hi=2), Verdict.OK),
        (Range(lo=2, hi=4), Verdict.DIFFICULT),
        (Range(lo=3, hi=6), Verdict.UNCERTAIN),
        (Range(lo=5, hi=8), Verdict.BLOCKER),
    ],
)
def test_check_max(value, expected):
    assert check_max(value, MaxThreshold(soft=2, hard=4), "x").verdict == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, Verdict.UNKNOWN),
        (Range.exact(95), Verdict.OK),
        (Range.exact(85), Verdict.DIFFICULT),
        (Range(lo=70, hi=85), Verdict.UNCERTAIN),
        (Range.exact(70), Verdict.BLOCKER),
    ],
)
def test_check_min(value, expected):
    assert check_min(value, MinThreshold(soft=90, hard=80), "x").verdict == expected


def test_raised_kerb_depends_on_profile():
    attrs = {"kind": "raised", "height_cm": {"lo": 8, "hi": 15}}
    assert evaluate(FeatureType.KERB, attrs, WHEELCHAIR).verdict == Verdict.BLOCKER
    assert evaluate(FeatureType.KERB, attrs, STROLLER).verdict == Verdict.UNCERTAIN
    assert evaluate(FeatureType.KERB, attrs, BLIND) is None  # nieistotne dla profilu


def test_lowered_kerb_ok():
    assert evaluate(FeatureType.KERB, {"kind": "lowered"}, WHEELCHAIR).verdict == Verdict.OK


def test_missing_data_is_never_ok():
    for ftype in (FeatureType.KERB, FeatureType.SURFACE, FeatureType.ENTRANCE, FeatureType.PATH_WIDTH):
        assert evaluate(ftype, {}, WHEELCHAIR).verdict == Verdict.UNKNOWN


def test_steps_without_count_block_wheelchair_but_not_with_ramp():
    assert evaluate(FeatureType.STEPS, {}, WHEELCHAIR).verdict == Verdict.BLOCKER
    assert evaluate(FeatureType.STEPS, {"ramp": True}, WHEELCHAIR).verdict == Verdict.OK


def test_few_steps_are_difficult_for_stroller():
    assert evaluate(FeatureType.STEPS, {"count": 2}, STROLLER).verdict == Verdict.DIFFICULT


def test_steps_for_blind_check_contrast():
    assert evaluate(FeatureType.STEPS, {"contrast_marking": False}, BLIND).verdict == Verdict.DIFFICULT
    assert evaluate(FeatureType.STEPS, {"contrast_marking": True}, BLIND).verdict == Verdict.OK


def test_surface():
    assert evaluate(FeatureType.SURFACE, {"value": "sett"}, WHEELCHAIR).verdict == Verdict.DIFFICULT
    assert evaluate(FeatureType.SURFACE, {"value": "gravel"}, WHEELCHAIR).verdict == Verdict.BLOCKER
    assert evaluate(FeatureType.SURFACE, {"value": "asphalt"}, WHEELCHAIR).verdict == Verdict.OK


def test_good_ramp_is_amenity():
    result = evaluate(FeatureType.RAMP, {"incline_pct": 5, "width_cm": 120}, WHEELCHAIR)
    assert result.verdict == Verdict.AMENITY


def test_overhanging_obstacle_dangerous_for_blind():
    attrs = {"kind": "znak", "height_from_ground_cm": {"lo": 150, "hi": 170}, "blocks_path": False}
    assert evaluate(FeatureType.OBSTACLE, attrs, BLIND).verdict == Verdict.BLOCKER
    assert evaluate(FeatureType.OBSTACLE, attrs, WHEELCHAIR).verdict == Verdict.OK


def test_temporary_obstacle_is_marked():
    result = evaluate(FeatureType.OBSTACLE, {"kind": "rusztowanie", "blocks_path": True, "temporary": True}, WHEELCHAIR)
    assert result.verdict == Verdict.BLOCKER
    assert "(tymczasowe)" in result.reasons[0]


def test_crossing_for_blind():
    attrs = {"tactile_paving": True, "traffic_signals": True, "sound_signals": False}
    assert evaluate(FeatureType.CROSSING, attrs, BLIND).verdict == Verdict.DIFFICULT


def test_preset_overrides():
    needs = needs_from_preset("wheelchair_manual", {"mobility": {"max_edge_height_cm": {"soft": 10, "hard": 20}}})
    attrs = {"kind": "raised", "height_cm": {"lo": 8, "hi": 15}}
    assert evaluate(FeatureType.KERB, attrs, needs).verdict == Verdict.DIFFICULT
    assert needs.mobility.min_width_cm.soft == 90  # reszta presetu bez zmian
