"""Klasyfikacja barier wg potrzeb użytkownika. Bez I/O i bez zależności od bazy."""

from .classifier import Assessment, FeatureAssessment, Summary, assess, assess_feature, default_required
from .fusion import group_observations
from .models import Feature, FeatureType, GeoPoint, Observation, Range, Source, SourceType
from .needs import PRESETS, Needs, PresetInfo, needs_from_preset, preset_catalog
from .trust import DataStatus
from .verdict import Verdict

__all__ = [
    "PRESETS",
    "Assessment",
    "DataStatus",
    "Feature",
    "FeatureAssessment",
    "FeatureType",
    "GeoPoint",
    "Needs",
    "Observation",
    "PresetInfo",
    "Range",
    "Source",
    "SourceType",
    "Summary",
    "Verdict",
    "assess",
    "assess_feature",
    "default_required",
    "group_observations",
    "needs_from_preset",
    "preset_catalog",
]
