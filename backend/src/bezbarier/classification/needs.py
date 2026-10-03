"""Model potrzeb użytkownika.

Zgodnie z kryteriami wyzwania NIE pytamy o niepełnosprawność - profil to zestaw progów
i preferencji. Presety ("wózek", "niewidomy") to tylko wygodne wartości startowe,
które użytkownik może dowolnie zmienić.

Wartości w PRESETS są orientacyjne - do walidacji z użytkownikami.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class MaxThreshold(BaseModel):
    """Wartość <= soft: OK. soft < wartość <= hard: trudne. Wartość > hard: blokada."""

    soft: float
    hard: float

    @model_validator(mode="after")
    def _check(self) -> MaxThreshold:
        if self.soft > self.hard:
            raise ValueError("soft musi być <= hard")
        return self


class MinThreshold(BaseModel):
    """Wartość >= soft: OK. hard <= wartość < soft: trudne. Wartość < hard: blokada."""

    soft: float
    hard: float

    @model_validator(mode="after")
    def _check(self) -> MinThreshold:
        if self.soft < self.hard:
            raise ValueError("soft musi być >= hard")
        return self


class MobilityNeeds(BaseModel):
    max_edge_height_cm: MaxThreshold      # krawężnik, próg w drzwiach, pojedynczy uskok
    max_step_count: MaxThreshold          # liczba schodów bez podjazdu/windy
    min_width_cm: MinThreshold            # przejście, drzwi, podjazd
    max_incline_pct: MaxThreshold
    difficult_surfaces: set[str] = Field(default_factory=set)
    blocking_surfaces: set[str] = Field(default_factory=set)
    needs_handrail: bool = False


class VisionNeeds(BaseModel):
    needs_tactile_paving: bool = True
    needs_sound_signals: bool = True
    needs_step_contrast: bool = True
    # przeszkody "wiszące" w tym zakresie wysokości nie są wykrywalne białą laską
    overhang_min_cm: float = 70
    overhang_max_cm: float = 210


class Needs(BaseModel):
    preset: str | None = None
    mobility: MobilityNeeds | None = None
    vision: VisionNeeds | None = None
    wants: set[str] = Field(default_factory=set)  # udogodnienia: bench, toilets_wheelchair, elevator


_SURFACES_ROUGH = {"sett", "cobblestone", "unhewn_cobblestone"}
_SURFACES_LOOSE = {"gravel", "fine_gravel", "compacted", "grass", "sand", "dirt", "ground", "mud"}

PRESETS: dict[str, Needs] = {
    "wheelchair_manual": Needs(
        preset="wheelchair_manual",
        mobility=MobilityNeeds(
            max_edge_height_cm=MaxThreshold(soft=2, hard=4),
            max_step_count=MaxThreshold(soft=0, hard=0),
            min_width_cm=MinThreshold(soft=90, hard=80),
            max_incline_pct=MaxThreshold(soft=6, hard=8),
            difficult_surfaces={"sett", "cobblestone", "compacted", "fine_gravel"},
            blocking_surfaces={"unhewn_cobblestone", "gravel", "grass", "sand", "dirt", "ground", "mud"},
        ),
        wants={"toilets_wheelchair", "elevator"},
    ),
    "wheelchair_electric": Needs(
        preset="wheelchair_electric",
        mobility=MobilityNeeds(
            max_edge_height_cm=MaxThreshold(soft=3, hard=6),
            max_step_count=MaxThreshold(soft=0, hard=0),
            min_width_cm=MinThreshold(soft=95, hard=85),
            max_incline_pct=MaxThreshold(soft=8, hard=10),
            difficult_surfaces={"unhewn_cobblestone", "cobblestone", "gravel"},
            blocking_surfaces={"sand", "mud", "grass"},
        ),
        wants={"toilets_wheelchair", "elevator"},
    ),
    "stroller": Needs(
        preset="stroller",
        mobility=MobilityNeeds(
            max_edge_height_cm=MaxThreshold(soft=4, hard=12),
            max_step_count=MaxThreshold(soft=0, hard=3),  # kilka schodów da się wnieść - trudne
            min_width_cm=MinThreshold(soft=70, hard=60),
            max_incline_pct=MaxThreshold(soft=8, hard=15),
            difficult_surfaces=_SURFACES_ROUGH | {"gravel"},
            blocking_surfaces={"sand", "mud"},
        ),
        wants={"bench"},
    ),
    "low_mobility": Needs(
        preset="low_mobility",
        mobility=MobilityNeeds(
            max_edge_height_cm=MaxThreshold(soft=10, hard=20),
            max_step_count=MaxThreshold(soft=2, hard=20),
            min_width_cm=MinThreshold(soft=60, hard=45),
            max_incline_pct=MaxThreshold(soft=6, hard=12),
            difficult_surfaces={"unhewn_cobblestone", "gravel", "grass"} | {"sand", "mud"},
            blocking_surfaces=set(),
            needs_handrail=True,
        ),
        wants={"bench"},
    ),
    "blind": Needs(
        preset="blind",
        vision=VisionNeeds(),
    ),
}


def needs_from_preset(name: str, overrides: dict[str, Any] | None = None) -> Needs:
    """Preset + opcjonalne nadpisania, np. {"mobility": {"max_edge_height_cm": {"soft": 3, "hard": 5}}}."""
    if name not in PRESETS:
        raise KeyError(f"Nieznany preset: {name}. Dostępne: {', '.join(PRESETS)}")
    base = PRESETS[name].model_dump()
    if overrides:
        base = _deep_merge(base, overrides)
    return Needs.model_validate(base)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
