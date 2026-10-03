"""Repozytorium obserwacji w pamięci. Docelowo PostgreSQL + PostGIS z tym samym interfejsem."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .classification.fusion import haversine_m
from .classification.models import GeoPoint, Observation


class Place(BaseModel):
    id: str
    name: str
    location: GeoPoint
    sample: bool = False


class InMemoryRepository:
    def __init__(self) -> None:
        self.places: dict[str, Place] = {}
        self._observations: dict[str, Observation] = {}

    def add_place(self, place: Place) -> None:
        self.places[place.id] = place

    def add_observations(self, observations: list[Observation]) -> None:
        for obs in observations:
            self._observations[obs.id] = obs

    def observations_for_place(self, place_id: str, radius_m: float = 30) -> list[Observation]:
        """Obserwacje przypisane do miejsca + te z najbliższego otoczenia (chodnik, krawężnik przed wejściem)."""
        place = self.places[place_id]
        return [
            obs
            for obs in self._observations.values()
            if obs.place_id == place_id
            or (obs.place_id is None and haversine_m(obs.location, place.location) <= radius_m)
        ]

    @classmethod
    def from_file(cls, path: Path) -> InMemoryRepository:
        data = json.loads(path.read_text(encoding="utf-8"))
        repo = cls()
        for p in data.get("places", []):
            repo.add_place(Place.model_validate(p))
        repo.add_observations([Observation.model_validate(o) for o in data.get("observations", [])])
        return repo
