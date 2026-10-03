"""API klasyfikacji barier. Uruchomienie: uvicorn bezbarier.api.main:app --reload"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..classification import (
    PRESETS,
    Assessment,
    FeatureType,
    Needs,
    Observation,
    assess,
    default_required,
    group_observations,
    needs_from_preset,
)
from ..detection import ImageRef, detections_to_observations, get_detector
from ..storage import InMemoryRepository, Place

SAMPLE_DATA = Path(__file__).resolve().parents[3] / "data" / "sample_observations.json"

app = FastAPI(title="Kraków bez barier - klasyfikacja barier")
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


def _resolve_needs(req: ProfileRequest) -> Needs:
    if req.needs is not None:
        return req.needs
    if req.preset is not None:
        try:
            return needs_from_preset(req.preset, req.overrides)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    raise HTTPException(status_code=400, detail="Podaj 'preset' albo 'needs'")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/presets")
def presets() -> dict[str, Needs]:
    return PRESETS


@app.post("/classify")
def classify(req: ClassifyRequest) -> Assessment:
    """Czysta klasyfikacja: obserwacje + profil -> ocena. Nic nie zapisuje."""
    needs = _resolve_needs(req)
    required = req.required if req.required is not None else default_required(needs, "place")
    return assess(group_observations(req.observations), needs, req.today, required)


@app.get("/places")
def places() -> list[Place]:
    return list(repo.places.values())


@app.post("/places/{place_id}/assessment")
def place_assessment(place_id: str, req: ProfileRequest) -> Assessment:
    if place_id not in repo.places:
        raise HTTPException(status_code=404, detail=f"Nie ma miejsca {place_id}")
    needs = _resolve_needs(req)
    features = group_observations(repo.observations_for_place(place_id))
    return assess(features, needs, req.today, default_required(needs, "place"))


@app.post("/observations/analyze")
def analyze(req: AnalyzeRequest) -> list[Observation]:
    """Uruchamia detektor na zdjęciu i zapisuje obserwacje (niezależne od profilu)."""
    detector = get_detector()
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
