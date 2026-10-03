"""Wspólne dla detektorów opartych o modele wizyjne (Claude, Gemini):
prompt z taksonomią cech, schemat odpowiedzi JSON i konwersja na Detection.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel

from ..classification.models import FeatureType
from .base import Detection, ImageRef

SurfaceValue = Literal[
    "asphalt", "concrete", "paving_stones", "sett", "cobblestone", "unhewn_cobblestone",
    "gravel", "fine_gravel", "compacted", "grass", "sand", "dirt", "wood",
]

# Wartości OSM (https://wiki.openstreetmap.org/wiki/Key:surface) z opisem wyglądu - bez opisu model je myli
SURFACE_GLOSSARY: dict[str, str] = {
    "asphalt": "asfalt - gładka, ciemna, jednolita powierzchnia bez fug",
    "concrete": "beton - gładka szara wylewka lub duże płyty z rzadkimi szczelinami",
    "paving_stones": "płyty chodnikowe lub betonowa kostka (np. polbruk) - regularne, płaskie elementy z wąskimi "
    "fugami; także gładkie, cięte płyty kamienne",
    "sett": "kostka kamienna (np. granitowa) - małe ciosane kamienne kostki ok. 10x10 cm, lekko nierówne, wyraźne fugi",
    "cobblestone": "bruk - tylko gdy nie da się rozróżnić sett od unhewn_cobblestone",
    "unhewn_cobblestone": "kocie łby - zaokrąglone, nieobrobione kamienie, bardzo nierówna powierzchnia",
    "gravel": "żwir - luźne kamyki",
    "fine_gravel": "drobny żwir / grys - ubita, drobnoziarnista nawierzchnia parkowa",
    "compacted": "utwardzona ziemia lub tłuczeń - twarda, matowa, bez wyraźnych kamyków",
    "grass": "trawa",
    "sand": "piasek",
    "dirt": "ubita ziemia, ścieżka gruntowa",
    "wood": "drewno - deski, pomost",
}

_PROMPT_TEMPLATE = """\
Analizujesz zdjęcia ulic (perspektywa pieszego) pod kątem dostępności dla osób z różnymi potrzebami
(wózki inwalidzkie, wózki dziecięce, osoby niewidome, osoby starsze).

Wypisz KAŻDĄ widoczną cechę z poniższej listy typów:
- kerb: krawężnik (kerb_kind: raised/lowered/flush, height_cm_min/max = wysokość)
- steps: schody (step_count_min/max, height_cm_min/max = wysokość stopnia, handrail, contrast_marking, ramp_present)
- ramp: podjazd (incline_pct_min/max, width_cm_min/max)
- entrance: wejście do budynku (width_cm_min/max = szerokość drzwi, height_cm_min/max = wysokość progu)
- surface: nawierzchnia, po której się idzie (surface: jedna wartość ze słowniczka poniżej)
- path_width: szerokość chodnika (width_cm_min/max)
- incline: wyraźne nachylenie chodnika (incline_pct_min/max)
- obstacle: przeszkoda (obstacle_kind, blocks_path, width_cm_min/max = wolne miejsce obok przeszkody,
  bottom_height_cm_min/max = wysokość dolnej krawędzi, jeśli przeszkoda wisi nad chodnikiem, temporary)
- crossing: przejście dla pieszych (kerb_kind i height_cm_* dla krawężnika, tactile_paving, traffic_signals, sound_signals)
- amenity: udogodnienie (amenity_kind: bench, toilets_wheelchair, elevator)

Słowniczek nawierzchni - wybieraj na podstawie WYGLĄDU z opisu, nie potocznej nazwy:
{surface_glossary}
"Kostka brukowa" bywa betonowa (paving_stones) albo kamienna (sett): małe ciosane kostki z kamienia naturalnego
-> sett; regularne betonowe kostki lub gładkie płyty -> paving_stones. Przy mieszance wybierz nawierzchnię głównego
ciągu pieszego, a mieszankę opisz w description.

Zasady:
- Wymiary szacuj jako zakres min-max, korzystając z obiektów referencyjnych: standardowy krawężnik ~12-15 cm,
  stopień schodów ~15-18 cm, drzwi ~200 cm wysokości, płyta chodnikowa 35x35 lub 50x50 cm.
- Jeśli czegoś nie widać lub nie da się ocenić - wpisz null. Nie zgaduj; null jest lepszy niż zmyślona wartość.
- confidence (0-1) to Twoja pewność, że cecha istnieje i atrybuty są poprawne.
- description: jedno krótkie zdanie po polsku, co widać.
- Pola nieużywane przez dany typ ustaw na null.
- Jeśli na zdjęciu nie ma żadnych z tych cech, zwróć pustą listę.
"""

SYSTEM_PROMPT = _PROMPT_TEMPLATE.format(
    surface_glossary="\n".join(f"  - {key}: {desc}" for key, desc in SURFACE_GLOSSARY.items())
)


class VlmDetection(BaseModel):
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
    surface: SurfaceValue | None
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


class VlmResult(BaseModel):
    detections: list[VlmDetection]


def to_range(lo: float | None, hi: float | None) -> dict[str, float] | None:
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return {"lo": min(lo, hi), "hi": max(lo, hi)}


def to_attrs(d: VlmDetection) -> dict[str, Any]:
    height = to_range(d.height_cm_min, d.height_cm_max)
    width = to_range(d.width_cm_min, d.width_cm_max)
    incline = to_range(d.incline_pct_min, d.incline_pct_max)
    by_type: dict[FeatureType, dict[str, Any]] = {
        FeatureType.KERB: {"kind": d.kerb_kind, "height_cm": height},
        FeatureType.STEPS: {
            "count": to_range(d.step_count_min, d.step_count_max),
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
            "height_from_ground_cm": to_range(d.bottom_height_cm_min, d.bottom_height_cm_max),
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


def media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"



def to_detections(result: VlmResult) -> list[Detection]:
    return [
        Detection(type=d.type, attrs=to_attrs(d), confidence=min(1.0, max(0.0, d.confidence)))
        for d in result.detections
    ]


def user_prompt(image: ImageRef) -> str:
    heading = f", kierunek kamery {image.heading:.0f}°" if image.heading is not None else ""
    return f"Zdjęcie: {image.provider}, {image.captured_at}{heading}. Wypisz cechy."


def load_image_bytes(image: ImageRef, image_bytes: bytes | None) -> bytes:
    if image_bytes is not None:
        return image_bytes
    if not image.url:
        raise ValueError("Potrzebny image_bytes albo image.url")
    resp = httpx.get(image.url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.content
