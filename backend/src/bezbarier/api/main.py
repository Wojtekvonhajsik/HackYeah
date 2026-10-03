"""API klasyfikacji barier. Uruchomienie: uvicorn bezbarier.api.main:app --reload"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, computed_field

from ..classification import (
    Assessment,
    FeatureType,
    GeoPoint,
    Needs,
    Observation,
    PresetInfo,
    SourceType,
    assess,
    default_required,
    group_observations,
    needs_from_preset,
    preset_catalog,
)
from ..classification.describe import describe
from ..classification.report_schema import validate_report
from ..detection import Detector, ImageRef, detections_to_observations, get_detector
from ..safety import PlaceSafety, assess_safety
from ..scan import ScanResult, scan_images
from ..sources import gus, mapillary, osm
from ..storage import PLACE_RADIUS_M, Place, SqliteRepository, Vote, VoteValue
from .security import limit_votes, require_admin

BACKEND_DIR = Path(__file__).resolve().parents[3]
SAMPLE_DATA = BACKEND_DIR / "data" / "sample_observations.json"
WEB_DIR = BACKEND_DIR / "web"  # aplikacja webowa (mobile-first), serwowana pod /app/

# Klucze (MAPILLARY_TOKEN, GEMINI_API_KEY, DETECTOR...) z backend/.env - plik jest w .gitignore
load_dotenv(BACKEND_DIR / ".env")
if not os.environ.get("ADMIN_TOKEN"):
    logging.getLogger(__name__).warning(
        "Brak ADMIN_TOKEN - skan zdjęć (płatne API) jest dostępny dla każdego. Ustaw go przed wdrożeniem publicznym."
    )

app = FastAPI(title="Kraków bez barier - klasyfikacja barier")
app.add_middleware(
    CORSMiddleware,
    # prototyp: domyślnie wszystko; na produkcji CORS_ORIGINS=https://twoja-domena.pl
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
# Baza: plik SQLite (głosy, poprawki, wyniki skanów i dane z OSM przetrwają restart). ":memory:" = bez zapisu.
DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BACKEND_DIR / "data" / "bezbarier.db"))
repo = SqliteRepository(DATABASE_PATH)
if SAMPLE_DATA.exists():
    repo.load_file(SAMPLE_DATA)


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


class ReportItem(BaseModel):
    type: FeatureType
    attrs: dict[str, Any]


class OwnerReportRequest(BaseModel):
    reports: list[ReportItem] = Field(min_length=1, max_length=10)


class OwnerCodeRequest(BaseModel):
    place_id: str
    owner_name: str = Field(min_length=2, max_length=80)


class OwnerCodeResponse(BaseModel):
    place_id: str
    place_name: str
    owner_name: str
    code: str       # przekaż właścicielowi - nie da się go później odczytać z bazy
    form_path: str  # strona formularza w aplikacji


def _validated(feature_type: FeatureType, attrs: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_report(feature_type, attrs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Niepoprawne zgłoszenie: {e}") from e


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
    return RedirectResponse("/app/" if WEB_DIR.exists() else "/docs")


if WEB_DIR.exists():
    app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="app")


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
    """Wyszukiwanie miejsc w Krakowie (OpenStreetMap / Nominatim).

    Dla pierwszych wyników od razu zaczynamy w tle pobierać dane z OSM - zanim użytkownik kliknie,
    część danych zwykle już jest.
    """
    try:
        results = osm.search_places(q)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="Wyszukiwarka OpenStreetMap jest niedostępna, spróbuj później") from e
    found = []
    for r in results:
        place = _place_from_nominatim(r)
        repo.add_place(place)
        found.append(repo.places[place.id])
    for place in found[:PREFETCH_RESULTS]:
        _ensure_osm(place, date.today())
    return found


def _place_from_nominatim(r: dict[str, Any]) -> Place:
    return Place(
        id=f"osm-{r['osm_type']}-{r['osm_id']}",
        name=r.get("name") or r["display_name"].split(",")[0],
        address=r["display_name"],
        location=GeoPoint(lat=float(r["lat"]), lon=float(r["lon"])),
        osm_type=r["osm_type"],
        osm_id=int(r["osm_id"]),
        kind=f"{r['category']}:{r['type']}" if r.get("category") and r.get("type") else None,
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


class PlaceAssessment(Assessment):
    """Ocena miejsca + analiza bezpieczeństwa (pokazywana jako pierwsza)."""

    safety: PlaceSafety | None = None

    @computed_field
    @property
    def text(self) -> str:
        lines = []
        if self.safety is not None:
            lines.append(f"Bezpieczeństwo: {self.safety.headline}")
            lines += [f"{fact}." for fact in self.safety.facts]
            lines += self.safety.tips
        return "\n".join([*lines, describe(self)])


def _city_safety():
    try:
        return gus.city_safety()
    except (httpx.HTTPError, ValueError, KeyError):
        return None  # bez GUS ocena działa dalej, sekcja bezpieczeństwa mówi "brak danych"


@app.post("/places/{place_id}/assessment")
def place_assessment(place_id: str, req: ProfileRequest, wait: bool = False) -> PlaceAssessment:
    """Ocena wraca od razu. Jeśli dane z OSM jeszcze się pobierają, pending_sources = ["OpenStreetMap"] -
    frontend pokazuje wynik i odpytuje ponownie co kilka sekund. wait=true czeka na OSM (skrypty)."""
    needs = _resolve_needs(req)
    state = _ensure_osm(_get_place(place_id), req.today or date.today(), wait_s=OSM_WAIT_S if wait else 0)
    warnings = state.warnings + ([OSM_PENDING_MESSAGE] if state.pending else [])
    observations = repo.observations_for_place(place_id)
    result = assess(group_observations(observations), needs, req.today, default_required(needs, "place"), warnings,
                    place_id=place_id)
    result.warnings.extend(state.notes)  # informacja dla użytkownika, ale nie obniża oceny
    result.pending_sources = ["OpenStreetMap"] if state.pending else []
    safety = assess_safety(_city_safety(), observations, needs)
    return PlaceAssessment.model_validate({**result.model_dump(exclude={"text"}), "safety": safety})


# ---------- pobieranie danych z OSM w tle ----------

PREFETCH_RESULTS = 2         # dla tylu pierwszych wyników wyszukiwania pobieramy OSM od razu
OSM_RETRY_COOLDOWN_S = 120   # po nieudanym pobraniu nie próbujemy ponownie przez tyle sekund
OSM_WAIT_S = 45              # maks. czekanie przy wait=true
OSM_PENDING_MESSAGE = "Pobieramy dane z OpenStreetMap - ocena uzupełni się za chwilę."

osm_executor: Executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="osm-load")
_osm_jobs: dict[str, Future] = {}
_osm_failed_at: dict[str, float] = {}
_osm_lock = threading.Lock()


class OsmState(BaseModel):
    pending: bool = False
    warnings: list[str] = Field(default_factory=list)  # brak danych z OSM - ocena nie może być "brak barier"
    notes: list[str] = Field(default_factory=list)     # odświeżenie nieudane, ale są wcześniejsze dane


def _osm_needs_load(place: Place, today: date) -> bool:
    has_osm = bool(place.osm_type and place.osm_id)
    refresh_days = int(os.environ.get("OSM_REFRESH_DAYS", "30"))
    stale = place.data_loaded_at is None or (today - place.data_loaded_at).days >= refresh_days
    return not place.data_loaded or (has_osm and stale)


def _load_osm(place: Place, today: date) -> None:
    place_osm = (place.osm_type, place.osm_id) if place.osm_type and place.osm_id else None
    try:
        repo.add_observations(osm.fetch_place_observations(place.id, place.location, place_osm))
    except httpx.HTTPError as e:
        _osm_failed_at[place.id] = time.monotonic()
        logging.getLogger(__name__).warning("OSM dla %s nieudane: %r", place.id, e)
        raise
    repo.mark_loaded(place.id, today)


def _failure_state(place: Place, error: BaseException) -> OsmState:
    reason = osm.describe_error(error) if isinstance(error, httpx.HTTPError) else "błąd"
    if place.data_loaded:
        note = f"Nie udało się odświeżyć danych z OpenStreetMap ({reason}) - pokazujemy dane z {place.data_loaded_at}."
        return OsmState(notes=[note])
    warning = f"Nie udało się pobrać danych z OpenStreetMap ({reason}) - pokazujemy tylko dane zapisane wcześniej."
    return OsmState(warnings=[warning])


def _ensure_osm(place: Place, today: date, wait_s: float = 0) -> OsmState:
    """Uruchamia (raz) pobieranie OSM w tle i zwraca jego stan - nigdy nie blokuje dłużej niż wait_s."""
    if not _osm_needs_load(place, today):
        return OsmState()
    with _osm_lock:
        job = _osm_jobs.get(place.id)
        recently_failed = time.monotonic() - _osm_failed_at.get(place.id, -1e9) < OSM_RETRY_COOLDOWN_S
        if job is None or (job.done() and not recently_failed):
            job = osm_executor.submit(_load_osm, place, today)
            _osm_jobs[place.id] = job
    if wait_s:
        futures_wait([job], timeout=wait_s)
    if not job.done():
        return OsmState(pending=True)
    if (error := job.exception()) is not None:
        return _failure_state(repo.places[place.id], error)
    return OsmState()


@app.get("/stats/city")
def city_stats() -> gus.CityStats:
    """Dane GUS dla miasta: skala potrzeb (osoby z niepełnosprawnościami, seniorzy) i dostępność bazy noclegowej."""
    try:
        return gus.city_stats()
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise HTTPException(status_code=503, detail="Dane GUS są chwilowo niedostępne") from e


SCAN_MAX_RADIUS_M = 100


@app.post("/places/{place_id}/scan", dependencies=[Depends(require_admin)])
def scan_place(place_id: str, max_images: int = 5, radius_m: float = 25) -> ScanResult:
    """Pobiera zdjęcia z Mapillary wokół miejsca, wykrywa na nich cechy i zapisuje je jako obserwacje AI.

    Każde zdjęcie jest analizowane tylko raz. Po skanie wywołaj /assessment, żeby zobaczyć ocenę.
    """
    if not 1 <= max_images <= 20:
        raise HTTPException(status_code=400, detail="max_images musi być w zakresie 1-20")
    place = _get_place(place_id)
    # Punkt z OSM bywa na środku dużego budynku (np. Sukiennice) - wtedy powiększamy obszar szukania zdjęć
    radius = radius_m
    while True:
        try:
            images = mapillary.images_near(place.location.lat, place.location.lon, radius_m=radius, limit=500)
        except KeyError as e:
            raise HTTPException(status_code=400, detail="Brak MAPILLARY_TOKEN - ustaw go w backend/.env") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise HTTPException(status_code=400, detail="Mapillary odrzuciło MAPILLARY_TOKEN - sprawdź token") from e
            raise HTTPException(status_code=503, detail="Mapillary jest niedostępne, spróbuj później") from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=503, detail="Mapillary jest niedostępne, spróbuj później") from e
        if images or radius >= SCAN_MAX_RADIUS_M:
            break
        radius = min(radius * 2, SCAN_MAX_RADIUS_M)
    result = scan_images(
        place_id, place.location, images, _detector(), repo.analyzed_images, max_images, link_beyond_m=PLACE_RADIUS_M
    )
    repo.add_observations(result.observations)
    repo.save_analyzed_images()
    return result.model_copy(update={"radius_m": radius})


@app.post("/observations/analyze", dependencies=[Depends(require_admin)])
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


@app.post("/observations/{observation_id}/votes", dependencies=[Depends(limit_votes)])
def vote(observation_id: str, req: VoteRequest) -> VoteResponse:
    """Użytkownik potwierdza ("confirm") albo zaprzecza ("deny") informacji. Opcjonalnie podaje poprawkę."""
    original = repo.get_observation(observation_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"Nie ma obserwacji {observation_id}")
    if req.correction_attrs is not None and req.value != VoteValue.DENY:
        raise HTTPException(status_code=400, detail="Poprawkę można dodać tylko do głosu 'deny'")
    correction_attrs = _validated(original.type, req.correction_attrs) if req.correction_attrs is not None else None
    today = date.today()
    updated = repo.add_vote(Vote(observation_id=observation_id, voter_id=req.voter_id, value=req.value, created_at=today))
    correction = None
    if correction_attrs is not None:
        correction = repo.add_correction(original, correction_attrs, today)
    return VoteResponse(observation=updated, correction=correction)


@app.post("/places/{place_id}/reports", dependencies=[Depends(limit_votes)])
def report(place_id: str, req: ReportItem) -> Observation:
    """Użytkownik uzupełnia brakującą informację o miejscu, np. szerokość drzwi. Trafia jako niepotwierdzone
    zgłoszenie - inni mogą je potwierdzić (3 potwierdzenia = status 'potwierdzone')."""
    place = _get_place(place_id)
    attrs = _validated(req.type, req.attrs)
    return repo.add_report(place, req.type, attrs, SourceType.USER_REPORT, "Zgłoszenie użytkownika", date.today())


@app.post("/admin/owner-codes", dependencies=[Depends(require_admin)])
def create_owner_code(req: OwnerCodeRequest) -> OwnerCodeResponse:
    """Kod dla właściciela obiektu (hotel, muzeum...) - wydawany np. po weryfikacji przy podpisaniu umowy."""
    place = _get_place(req.place_id)
    code = repo.create_owner_code(place.id, req.owner_name.strip())
    return OwnerCodeResponse(
        place_id=place.id,
        place_name=place.name,
        owner_name=req.owner_name.strip(),
        code=code,
        form_path=f"/app/#/wlasciciel/{place.id}",
    )


@app.post("/places/{place_id}/owner-reports", dependencies=[Depends(limit_votes)])
def owner_report(
    place_id: str, req: OwnerReportRequest, x_owner_code: str | None = Header(default=None)
) -> list[Observation]:
    """Właściciel podaje dane o swoim obiekcie (nagłówek X-Owner-Code). Źródło 'właściciel obiektu' ma wysoką
    wiarygodność i status 'potwierdzone'."""
    place = _get_place(place_id)
    owner = repo.owner_for_code(place.id, x_owner_code or "")
    if owner is None:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod właściciela dla tego miejsca")
    cleaned = [(item.type, _validated(item.type, item.attrs)) for item in req.reports]  # najpierw walidacja całości
    today = date.today()
    return [
        repo.add_report(place, feature_type, attrs, SourceType.OWNER, f"Właściciel: {owner}", today)
        for feature_type, attrs in cleaned
    ]
