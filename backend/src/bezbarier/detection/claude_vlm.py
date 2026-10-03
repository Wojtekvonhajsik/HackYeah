"""Detektor oparty o model wizyjny Claude ze structured outputs.

Model dostaje taksonomię cech i zwraca JSON zgodny ze schematem _VlmResult.
Wymiary podaje jako zakresy (min/max) - ze zdjęcia 2D nie da się zmierzyć dokładnie.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

import anthropic
from pydantic import BaseModel

from ..classification.models import FeatureType
from .base import Detection, ImageRef

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
Analizujesz zdjęcia ulic (perspektywa pieszego) pod kątem dostępności dla osób z różnymi potrzebami
(wózki inwalidzkie, wózki dziecięce, osoby niewidome, osoby starsze).

Wypisz KAŻDĄ widoczną cechę z poniższej listy typów:
- kerb: krawężnik (kerb_kind: raised/lowered/flush, height_cm_min/max = wysokość)
- steps: schody (step_count_min/max, height_cm_min/max = wysokość stopnia, handrail, contrast_marking, ramp_present)
- ramp: podjazd (incline_pct_min/max, width_cm_min/max)
- entrance: wejście do budynku (width_cm_min/max = szerokość drzwi, height_cm_min/max = wysokość progu)
- surface: nawierzchnia chodnika (surface: wartość OSM: asphalt, concrete, paving_stones, sett, cobblestone,
  unhewn_cobblestone, gravel, fine_gravel, compacted, grass, sand, dirt, wood)
- path_width: szerokość chodnika (width_cm_min/max)
- incline: wyraźne nachylenie chodnika (incline_pct_min/max)
- obstacle: przeszkoda (obstacle_kind, blocks_path, width_cm_min/max = wolne miejsce obok przeszkody,
  bottom_height_cm_min/max = wysokość dolnej krawędzi, jeśli przeszkoda wisi nad chodnikiem, temporary)
- crossing: przejście dla pieszych (kerb_kind i height_cm_* dla krawężnika, tactile_paving, traffic_signals, sound_signals)
- amenity: udogodnienie (amenity_kind: bench, toilets_wheelchair, elevator)

Zasady:
- Wymiary szacuj jako zakres min-max, korzystając z obiektów referencyjnych: standardowy krawężnik ~12-15 cm,
  stopień schodów ~15-18 cm, drzwi ~200 cm wysokości, płyta chodnikowa 35x35 lub 50x50 cm.
- Jeśli czegoś nie widać lub nie da się ocenić - wpisz null. Nie zgaduj; null jest lepszy niż zmyślona wartość.
- confidence (0-1) to Twoja pewność, że cecha istnieje i atrybuty są poprawne.
- description: jedno krótkie zdanie po polsku, co widać.
- Pola nieużywane przez dany typ ustaw na null.
- Jeśli na zdjęciu nie ma żadnych z tych cech, zwróć pustą listę.
"""


class _VlmDetection(BaseModel):
    # Wszystkie pola wymagane, ale nullable - prościej dla structured outputs niż pola opcjonalne.
    type: FeatureType
    confidence: float
    description: str
    kerb_kind: Literal["raised", "lowered", "flush"] | None
    height_cm_min: float | None
    height_cm_max: float | None
    step_count_min: int | None
    step_count_max: int | None
    width_cm_min: float | None
    width_cm_max: float | None
    incline_pct_min: float | None
    incline_pct_max: float | None
    bottom_height_cm_min: float | None
    bottom_height_cm_max: float | None
    surface: str | None
    handrail: bool | None
    contrast_marking: bool | None
    ramp_present: bool | None
    tactile_paving: bool | None
    traffic_signals: bool | None
    sound_signals: bool | None
    obstacle_kind: str | None
    blocks_path: bool | None
    temporary: bool | None
    amenity_kind: Literal["bench", "toilets_wheelchair", "elevator"] | None


class _VlmResult(BaseModel):
    detections: list[_VlmDetection]


def _range(lo: float | None, hi: float | None) -> dict[str, float] | None:
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return {"lo": min(lo, hi), "hi": max(lo, hi)}


def _to_attrs(d: _VlmDetection) -> dict[str, Any]:
    height = _range(d.height_cm_min, d.height_cm_max)
    width = _range(d.width_cm_min, d.width_cm_max)
    incline = _range(d.incline_pct_min, d.incline_pct_max)
    by_type: dict[FeatureType, dict[str, Any]] = {
        FeatureType.KERB: {"kind": d.kerb_kind, "height_cm": height},
        FeatureType.STEPS: {
            "count": _range(d.step_count_min, d.step_count_max),
            "step_height_cm": height,
            "handrail": d.handrail,
            "contrast_marking": d.contrast_marking,
            "ramp": d.ramp_present,
        },
        FeatureType.RAMP: {"incline_pct": incline, "width_cm": width},
        FeatureType.ENTRANCE: {"width_cm": width, "threshold_cm": height},
        FeatureType.SURFACE: {"value": d.surface},
        FeatureType.PATH_WIDTH: {"width_cm": width},
        FeatureType.INCLINE: {"incline_pct": incline},
        FeatureType.OBSTACLE: {
            "kind": d.obstacle_kind,
            "blocks_path": d.blocks_path,
            "remaining_width_cm": width,
            "height_from_ground_cm": _range(d.bottom_height_cm_min, d.bottom_height_cm_max),
            "temporary": d.temporary,
        },
        FeatureType.CROSSING: {
            "kerb": d.kerb_kind,
            "kerb_height_cm": height,
            "tactile_paving": d.tactile_paving,
            "traffic_signals": d.traffic_signals,
            "sound_signals": d.sound_signals,
        },
        FeatureType.AMENITY: {"kind": d.amenity_kind},
    }
    attrs = {k: v for k, v in by_type[d.type].items() if v is not None}
    attrs["description"] = d.description
    return attrs


def _media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


class ClaudeVisionDetector:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str = MODEL):
        self.client = client or anthropic.Anthropic()
        self.model_name = model

    def detect(self, image: ImageRef, image_bytes: bytes | None = None) -> list[Detection]:
        if image_bytes is not None:
            source = {
                "type": "base64",
                "media_type": _media_type(image_bytes),
                "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
            }
        elif image.url:
            source = {"type": "url", "url": image.url}
        else:
            raise ValueError("Potrzebny image_bytes albo image.url")

        heading = f", kierunek kamery {image.heading:.0f}°" if image.heading is not None else ""
        response = self.client.messages.parse(
            model=self.model_name,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": source},
                    {"type": "text", "text": f"Zdjęcie: {image.provider}, {image.captured_at}{heading}. Wypisz cechy."},
                ],
            }],
            output_format=_VlmResult,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return []
        return [
            Detection(type=d.type, attrs=_to_attrs(d), confidence=min(1.0, max(0.0, d.confidence)))
            for d in response.parsed_output.detections
        ]
