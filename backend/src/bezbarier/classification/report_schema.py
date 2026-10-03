"""Walidacja atrybutów zgłaszanych przez ludzi (użytkownicy, właściciele obiektów).

Zgłoszenia trafiają prosto do oceny, więc przyjmujemy tylko znane pola w rozsądnych zakresach -
inaczej jedno złośliwe lub pomyłkowe zgłoszenie (np. "szerokość 9999 cm") zepsułoby wynik.
"""

from __future__ import annotations

from typing import Any

from .models import FeatureType
from .rules import AMENITY_PL, SURFACE_PL

KERB_KINDS = ("raised", "lowered", "flush")


def _number(lo: float, hi: float, integer: bool = False):
    def check(value: Any) -> float | int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("musi być liczbą")
        if not lo <= value <= hi:
            raise ValueError(f"musi być w zakresie {lo:g}-{hi:g}")
        if integer:
            if value != int(value):
                raise ValueError("musi być liczbą całkowitą")
            return int(value)
        return float(value)
    return check


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("musi być tak/nie")
    return value


def _choice(options):
    def check(value: Any) -> str:
        if value not in options:
            raise ValueError(f"musi być jedną z wartości: {', '.join(options)}")
        return value
    return check


def _text(max_len: int):
    def check(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("musi być tekstem")
        return value.strip()[:max_len]
    return check


CM = _number(0, 1000)
PCT = _number(0, 100)

FIELDS: dict[FeatureType, dict[str, Any]] = {
    FeatureType.KERB: {"kind": _choice(KERB_KINDS), "height_cm": _number(0, 100)},
    FeatureType.STEPS: {
        "count": _number(0, 500, integer=True),
        "ramp": _bool,
        "elevator": _bool,
        "handrail": _bool,
        "contrast_marking": _bool,
    },
    FeatureType.RAMP: {"incline_pct": PCT, "width_cm": CM},
    FeatureType.ENTRANCE: {"width_cm": _number(20, 1000), "threshold_cm": _number(0, 100), "automatic_door": _bool},
    FeatureType.SURFACE: {"value": _choice(tuple(SURFACE_PL))},
    FeatureType.PATH_WIDTH: {"width_cm": CM},
    FeatureType.INCLINE: {"incline_pct": PCT},
    FeatureType.OBSTACLE: {
        "kind": _text(40),
        "blocks_path": _bool,
        "remaining_width_cm": CM,
        "height_from_ground_cm": _number(0, 500),
        "temporary": _bool,
    },
    FeatureType.CROSSING: {
        "kerb": _choice(KERB_KINDS),
        "kerb_height_cm": _number(0, 100),
        "tactile_paving": _bool,
        "traffic_signals": _bool,
        "sound_signals": _bool,
    },
    FeatureType.AMENITY: {"kind": _choice(tuple(AMENITY_PL))},
}


def validate_report(feature_type: FeatureType, attrs: dict[str, Any]) -> dict[str, Any]:
    """Zwraca oczyszczone atrybuty albo rzuca ValueError z opisem po polsku."""
    allowed = FIELDS[feature_type]
    unknown = sorted(set(attrs) - set(allowed))
    if unknown:
        raise ValueError(f"nieznane pola dla '{feature_type.value}': {', '.join(unknown)}")
    cleaned: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        try:
            cleaned[key] = allowed[key](value)
        except ValueError as e:
            raise ValueError(f"pole '{key}' {e}") from e
    if not cleaned:
        raise ValueError("podaj co najmniej jedną informację")
    if feature_type == FeatureType.AMENITY and "kind" not in cleaned:
        raise ValueError("podaj rodzaj udogodnienia")
    return cleaned
