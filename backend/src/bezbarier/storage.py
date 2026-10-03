"""Repozytorium obserwacji: w pamięci (testy) albo z zapisem do SQLite. Docelowo PostGIS z tym samym interfejsem."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .classification.fusion import haversine_m
from .classification.models import FeatureType, GeoPoint, Observation, Source, SourceType


# Obserwacje bez place_id w tym promieniu od miejsca należą do jego otoczenia (chodnik, krawężnik)
PLACE_RADIUS_M = 30


class Place(BaseModel):
    id: str
    name: str
    location: GeoPoint
    address: str | None = None
    osm_type: str | None = None   # "node" / "way" / "relation"
    osm_id: int | None = None
    sample: bool = False
    data_loaded: bool = True      # False = dane z OSM pobierzemy przy pierwszej ocenie
    data_loaded_at: date | None = None  # kiedy ostatnio pobrano dane z OSM (do odświeżania)


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
            place = place.model_copy(update={"data_loaded": True, "data_loaded_at": existing.data_loaded_at})
        self.places[place.id] = place

    def mark_loaded(self, place_id: str, when: date | None = None) -> None:
        self.places[place_id] = self.places[place_id].model_copy(
            update={"data_loaded": True, "data_loaded_at": when or date.today()}
        )

    def add_observations(self, observations: list[Observation]) -> None:
        for obs in observations:
            self._observations[obs.id] = obs

    def get_observation(self, observation_id: str) -> Observation | None:
        obs = self._observations.get(observation_id)
        return self._with_votes(obs) if obs is not None else None

    def observations_for_place(self, place_id: str, radius_m: float = PLACE_RADIUS_M) -> list[Observation]:
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
                or obs.near_place_id == place_id
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

    def save_analyzed_images(self) -> None:
        """Wywoływane po skanie. W pamięci nic do zrobienia; SqliteRepository zapisuje do bazy."""

    def load_file(self, path: Path) -> None:
        """Wczytuje miejsca i obserwacje z pliku JSON (np. dane przykładowe)."""
        data = json.loads(path.read_text(encoding="utf-8"))
        for p in data.get("places", []):
            self.add_place(Place.model_validate(p))
        self.add_observations([Observation.model_validate(o) for o in data.get("observations", [])])

    @classmethod
    def from_file(cls, path: Path) -> InMemoryRepository:
        repo = cls()
        repo.load_file(path)
        return repo


_SCHEMA = """
CREATE TABLE IF NOT EXISTS places (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS votes (
    observation_id TEXT NOT NULL,
    voter_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (observation_id, voter_id)
);
CREATE TABLE IF NOT EXISTS analyzed_images (key TEXT PRIMARY KEY);
"""


def _migrate(obs: Observation) -> Observation:
    """Starsze skany przypisywały dalekie zdjęcia przez place_id - teraz to okolica (near_place_id).
    Ze skanu do samego miejsca należą tylko wejścia."""
    if obs.source.type == SourceType.AI_DETECTION and obs.type != FeatureType.ENTRANCE and obs.place_id:
        return obs.model_copy(update={"place_id": None, "near_place_id": obs.place_id})
    return obs


class SqliteRepository(InMemoryRepository):
    """Ta sama logika co w pamięci, ale każdy zapis trafia też do pliku SQLite.

    Przy starcie wczytujemy całą bazę do pamięci - na skalę prototypu (tysiące obserwacji) to wystarczy.
    Rekordy trzymamy jako JSON, więc zmiana modeli nie wymaga migracji schematu.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._load()

    def _load(self) -> None:
        for (data,) in self._db.execute("SELECT data FROM places"):
            place = Place.model_validate_json(data)
            self.places[place.id] = place
        for (data,) in self._db.execute("SELECT data FROM observations"):
            obs = _migrate(Observation.model_validate_json(data))
            self._observations[obs.id] = obs
        for (data,) in self._db.execute("SELECT data FROM votes"):
            vote = Vote.model_validate_json(data)
            self._votes.setdefault(vote.observation_id, {})[vote.voter_id] = vote
        self.analyzed_images.update(key for (key,) in self._db.execute("SELECT key FROM analyzed_images"))

    def _write(self, sql: str, rows: list[tuple[str, ...]]) -> None:
        with self._lock, self._db:  # "with self._db" = transakcja z automatycznym commit
            self._db.executemany(sql, rows)

    def add_place(self, place: Place) -> None:
        super().add_place(place)
        stored = self.places[place.id]
        self._write("INSERT OR REPLACE INTO places VALUES (?, ?)", [(stored.id, stored.model_dump_json())])

    def mark_loaded(self, place_id: str, when: date | None = None) -> None:
        super().mark_loaded(place_id, when)
        place = self.places[place_id]
        self._write("INSERT OR REPLACE INTO places VALUES (?, ?)", [(place.id, place.model_dump_json())])

    def add_observations(self, observations: list[Observation]) -> None:
        super().add_observations(observations)
        self._write(
            "INSERT OR REPLACE INTO observations VALUES (?, ?)",
            [(obs.id, obs.model_dump_json()) for obs in observations],
        )

    def add_vote(self, vote: Vote) -> Observation:
        result = super().add_vote(vote)
        self._write(
            "INSERT OR REPLACE INTO votes VALUES (?, ?, ?)",
            [(vote.observation_id, vote.voter_id, vote.model_dump_json())],
        )
        return result

    def save_analyzed_images(self) -> None:
        self._write("INSERT OR IGNORE INTO analyzed_images VALUES (?)", [(key,) for key in self.analyzed_images])

    def close(self) -> None:
        self._db.close()
