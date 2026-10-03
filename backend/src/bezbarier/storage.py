"""Repozytorium obserwacji w pamięci. Docelowo PostgreSQL + PostGIS z tym samym interfejsem."""

from __future__ import annotations

import json
import uuid
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .classification.fusion import haversine_m
from .classification.models import GeoPoint, Observation, Source, SourceType


class Place(BaseModel):
    id: str
    name: str
    location: GeoPoint
    address: str | None = None
    osm_type: str | None = None   # "node" / "way" / "relation"
    osm_id: int | None = None
    sample: bool = False
    data_loaded: bool = True      # False = dane z OSM pobierzemy przy pierwszej ocenie


class VoteValue(str, Enum):
    CONFIRM = "confirm"  # "to prawda"
    DENY = "deny"        # "to nieprawda"


class Vote(BaseModel):
    observation_id: str
    voter_id: str        # losowy identyfikator urządzenia - bez kont i danych osobowych
    value: VoteValue
    created_at: date


class InMemoryRepository:
    def __init__(self) -> None:
        self.places: dict[str, Place] = {}
        self._observations: dict[str, Observation] = {}
        self._votes: dict[str, dict[str, Vote]] = {}  # observation_id -> voter_id -> głos
        self.analyzed_images: set[str] = set()  # "<provider>-<id>" - nie analizujemy zdjęcia dwa razy

    def add_place(self, place: Place) -> None:
        existing = self.places.get(place.id)
        if existing is not None and existing.data_loaded:
            place = place.model_copy(update={"data_loaded": True})
        self.places[place.id] = place

    def mark_loaded(self, place_id: str) -> None:
        self.places[place_id] = self.places[place_id].model_copy(update={"data_loaded": True})

    def add_observations(self, observations: list[Observation]) -> None:
        for obs in observations:
            self._observations[obs.id] = obs

    def get_observation(self, observation_id: str) -> Observation | None:
        obs = self._observations.get(observation_id)
        return self._with_votes(obs) if obs is not None else None

    def observations_for_place(self, place_id: str, radius_m: float = 30) -> list[Observation]:
        """Obserwacje przypisane do miejsca + te z najbliższego otoczenia (chodnik, krawężnik przed wejściem).

        Dane przykładowe trafiają tylko do miejsc przykładowych - nie mieszamy ich z prawdziwymi.
        """
        place = self.places[place_id]
        return [
            self._with_votes(obs)
            for obs in self._observations.values()
            if (place.sample or not obs.source.sample)
            and (
                obs.place_id == place_id
                or (obs.place_id is None and haversine_m(obs.location, place.location) <= radius_m)
            )
        ]

    def add_vote(self, vote: Vote) -> Observation:
        """Jeden głos na urządzenie na obserwację - kolejny głos nadpisuje poprzedni."""
        if vote.observation_id not in self._observations:
            raise KeyError(vote.observation_id)
        self._votes.setdefault(vote.observation_id, {})[vote.voter_id] = vote
        return self.get_observation(vote.observation_id)

    def add_correction(self, original: Observation, attrs: dict[str, Any], today: date) -> Observation:
        """Użytkownik mówi, jak jest naprawdę - nowa obserwacja ze źródłem 'zgłoszenie użytkownika'."""
        correction = Observation(
            id=f"user-{uuid.uuid4().hex[:12]}",
            type=original.type,
            attrs=attrs,
            location=original.location,
            place_id=original.place_id,
            source=Source(type=SourceType.USER_REPORT, name="Poprawka użytkownika", observed_at=today),
        )
        self.add_observations([correction])
        return correction

    def _with_votes(self, obs: Observation) -> Observation:
        votes = self._votes.get(obs.id)
        if not votes:
            return obs
        confirms = [v for v in votes.values() if v.value == VoteValue.CONFIRM]
        return obs.model_copy(update={
            "confirmations": len(confirms),
            "denials": len(votes) - len(confirms),
            "last_confirmed_at": max((v.created_at for v in confirms), default=None),
        })

    @classmethod
    def from_file(cls, path: Path) -> InMemoryRepository:
        data = json.loads(path.read_text(encoding="utf-8"))
        repo = cls()
        for p in data.get("places", []):
            repo.add_place(Place.model_validate(p))
        repo.add_observations([Observation.model_validate(o) for o in data.get("observations", [])])
        return repo
