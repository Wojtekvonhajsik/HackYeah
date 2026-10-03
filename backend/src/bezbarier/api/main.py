"""API klasyfikacji barier. Uruchomienie: uvicorn bezbarier.api.main:app --reload"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..classification import (
    Assessment,
    FeatureType,
    GeoPoint,
    Needs,
    Observation,
    PresetInfo,
    assess,
    default_required,
    group_observations,
    needs_from_preset,
    preset_catalog,
)
from ..detection import Detector, ImageRef, detections_to_observations, get_detector
from ..scan import ScanResult, scan_images
from ..sources import mapillary, osm
from ..storage import InMemoryRepository, Place, Vote, VoteValue

BACKEND_DIR = Path(__file__).resolve().parents[3]
SAMPLE_DATA = BACKEND_DIR / "data" / "sample_observations.json"

# Klucze (MAPILLARY_TOKEN, GEMINI_API_KEY, DETECTOR...) z backend/.env - plik jest w .gitignore
load_dotenv(BACKEND_DIR / ".env")

app = FastAPI(title="Kraków bez barier - klasyfikacja barier")
app.add_middleware(
    CORSMiddleware,
    # prototyp: domyślnie wszystko; na produkcji CORS_ORIGINS=https://twoja-domena.pl
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
repo = InMemoryRepository.from_file(SAMPLE_DATA) if SAMPLE_DATA.exists() else InMemoryRepository()


class ProfileRequest(BaseModel):
    """Podaj preset (opcjonalnie z overrides) albo pełne needs. Profil nie jest zapisywany na serwerze."""

    preset: str | None = None
    overrides: dict[str, Any] | None = None
    needs: Needs | None = None
    today: date | None = None


class ClassifyRequest(ProfileRequest):
    observations: list[Observation]
    required: list[FeatureType] | None = None


class AnalyzeRequest(BaseModel):
    image: ImageRef
    place_id: str | None = None


class VoteRequest(BaseModel):
    voter_id: str
    value: VoteValue
    # Przy "deny" można podać, jak jest naprawdę, np. {"kind": "lowered"} - powstanie nowa obserwacja
    correction_attrs: dict[str, Any] | None = None


class VoteResponse(BaseModel):
    observation: Observation
    correction: Observation | None = None


def _resolve_needs(req: ProfileRequest) -> Needs:
    if req.needs is not None:
        return req.needs
    if req.preset is not None:
        try:
            return needs_from_preset(req.preset, req.overrides)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    raise HTTPException(status_code=400, detail="Podaj 'preset' albo 'needs'")


def _detector() -> Detector:
    """Czytelny błąd zamiast 500, gdy detektor nie jest skonfigurowany."""
    kind = os.environ.get("DETECTOR", "mock")
    try:
        return get_detector()
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f'Brak biblioteki dla DETECTOR={kind}. W folderze backend: pip install -e ".[{kind}]" ({e})',
        ) from e
    except ValueError as e:  # np. brak klucza API
        raise HTTPException(status_code=400, detail=f"Nie da się uruchomić detektora {kind}: {e}") from e


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/presets")
def presets() -> dict[str, PresetInfo]:
    return preset_catalog()


@app.post("/classify")
def classify(req: ClassifyRequest) -> Assessment:
    """Czysta klasyfikacja: obserwacje + profil -> ocena. Nic nie zapisuje."""
    needs = _resolve_needs(req)
    required = req.required if req.required is not None else default_required(needs, "place")
    return assess(group_observations(req.observations), needs, req.today, required)


@app.get("/places")
def places() -> list[Place]:
    return list(repo.places.values())


@app.get("/places/search")
def search_places(q: str) -> list[Place]:
    """Wyszukiwanie miejsc w Krakowie (OpenStreetMap / Nominatim)."""
    try:
        results = osm.search_places(q)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="Wyszukiwarka OpenStreetMap jest niedostępna, spróbuj później") from e
    found = []
    for r in results:
        place = _place_from_nominatim(r)
        repo.add_place(place)
        found.append(repo.places[place.id])
    return found


def _place_from_nominatim(r: dict[str, Any]) -> Place:
    return Place(
        id=f"osm-{r['osm_type']}-{r['osm_id']}",
        name=r.get("name") or r["display_name"].split(",")[0],
        address=r["display_name"],
        location=GeoPoint(lat=float(r["lat"]), lon=float(r["lon"])),
        osm_type=r["osm_type"],
        osm_id=int(r["osm_id"]),
        data_loaded=False,
    )


def _get_place(place_id: str) -> Place:
    """Miejsce z pamięci; id w formacie osm-<typ>-<id> odtwarzamy z OSM (np. po restarcie serwera)."""
    if place_id in repo.places:
        return repo.places[place_id]
    m = re.fullmatch(r"osm-(node|way|relation)-(\d+)", place_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Nie ma miejsca {place_id}")
    try:
        result = osm.lookup_place(m.group(1), int(m.group(2)))
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="OpenStreetMap jest niedostępne, spróbuj później") from e
    if result is None:
        raise HTTPException(status_code=404, detail=f"Nie ma miejsca {place_id} w OpenStreetMap")
    repo.add_place(_place_from_nominatim(result))
    return repo.places[place_id]


@app.post("/places/{place_id}/assessment")
def place_assessment(place_id: str, req: ProfileRequest) -> Assessment:
    needs = _resolve_needs(req)
    warnings = _load_place_data(_get_place(place_id))
    features = group_observations(repo.observations_for_place(place_id))
    return assess(features, needs, req.today, default_required(needs, "place"), warnings)


def _load_place_data(place: Place) -> list[str]:
    """Przy pierwszej ocenie miejsca z OSM pobieramy cechy z otoczenia. Błąd źródła = ostrzeżenie, nie awaria."""
    if place.data_loaded:
        return []
    place_osm = (place.osm_type, place.osm_id) if place.osm_type and place.osm_id else None
    try:
        repo.add_observations(osm.fetch_place_observations(place.id, place.location, place_osm))
    except httpx.HTTPError:
        return ["Nie udało się pobrać danych z OpenStreetMap - pokazujemy tylko dane zapisane wcześniej."]
    repo.mark_loaded(place.id)
    return []


@app.post("/places/{place_id}/scan")
def scan_place(place_id: str, max_images: int = 5, radius_m: float = 25) -> ScanResult:
    """Pobiera zdjęcia z Mapillary wokół miejsca, wykrywa na nich cechy i zapisuje je jako obserwacje AI.

    Każde zdjęcie jest analizowane tylko raz. Po skanie wywołaj /assessment, żeby zobaczyć ocenę.
    """
    if not 1 <= max_images <= 20:
        raise HTTPException(status_code=400, detail="max_images musi być w zakresie 1-20")
    place = _get_place(place_id)
    try:
        images = mapillary.images_near(place.location.lat, place.location.lon, radius_m=radius_m, limit=50)
    except KeyError as e:
        raise HTTPException(status_code=400, detail="Brak MAPILLARY_TOKEN - ustaw go w backend/.env") from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="Mapillary jest niedostępne, spróbuj później") from e
    result = scan_images(place_id, place.location, images, _detector(), repo.analyzed_images, max_images)
    repo.add_observations(result.observations)
    return result


@app.post("/observations/analyze")
def analyze(req: AnalyzeRequest) -> list[Observation]:
    """Uruchamia detektor na zdjęciu i zapisuje obserwacje (niezależne od profilu)."""
    detector = _detector()
    detections = detector.detect(req.image)
    observations = detections_to_observations(
        req.image,
        detections,
        detector.model_name,
        req.place_id,
        sample=detector.model_name.startswith("mock"),  # wyniki mocka to dane przykładowe
    )
    repo.add_observations(observations)
    return observations


@app.post("/observations/{observation_id}/votes")
def vote(observation_id: str, req: VoteRequest) -> VoteResponse:
    """Użytkownik potwierdza ("confirm") albo zaprzecza ("deny") informacji. Opcjonalnie podaje poprawkę."""
    original = repo.get_observation(observation_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"Nie ma obserwacji {observation_id}")
    if req.correction_attrs is not None and req.value != VoteValue.DENY:
        raise HTTPException(status_code=400, detail="Poprawkę można dodać tylko do głosu 'deny'")
    today = date.today()
    updated = repo.add_vote(Vote(observation_id=observation_id, voter_id=req.voter_id, value=req.value, created_at=today))
    correction = None
    if req.correction_attrs is not None:
        correction = repo.add_correction(original, req.correction_attrs, today)
    return VoteResponse(observation=updated, correction=correction)
