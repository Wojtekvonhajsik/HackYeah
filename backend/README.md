# Backend - Kraków bez barier

Klasyfikacja barier wg potrzeb użytkownika. Projekt: [docs/klasyfikacja-barier.md](../docs/klasyfikacja-barier.md).

## Struktura

```
src/bezbarier/
  classification/   # rdzeń: model danych, profile, reguły, wiarygodność, fuzja, klasyfikator (bez I/O)
  detection/        # detektory zdjęć: MockDetector, ClaudeVisionDetector
  sources/          # adaptery źródeł: OpenStreetMap (Nominatim + Overpass), Mapillary
  storage.py        # repozytorium w pamięci (docelowo PostGIS)
  api/main.py       # FastAPI
data/sample_observations.json   # DANE PRZYKŁADOWE do demo
```

## Uruchomienie

Wymaga Pythona 3.11+.

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn bezbarier.api.main:app --reload
```

Dokumentacja API: http://localhost:8000/docs

Przykład:

```bash
curl -X POST localhost:8000/places/kawiarnia-rynek/assessment -H "Content-Type: application/json" -d '{"preset": "step_free_strict"}'
```

## Główne endpointy

| Endpoint | Co robi |
|---|---|
| `GET /presets` | profile potrzeb z etykietami dla użytkownika |
| `GET /places/search?q=...` | wyszukiwanie miejsc w Krakowie (OSM Nominatim) |
| `POST /places/{id}/assessment` | ocena miejsca dla profilu; przy pierwszym wywołaniu pobiera cechy z OSM (Overpass) |
| `POST /observations/{id}/votes` | potwierdzenie / zaprzeczenie informacji, opcjonalnie z poprawką |
| `POST /classify` | czysta klasyfikacja przesłanych obserwacji |
| `POST /observations/analyze` | detekcja cech na zdjęciu |

Frontend z innego portu: CORS jest domyślnie otwarty, na produkcji ustaw `CORS_ORIGINS`.
Publiczne serwery Overpass bywają przeciążone - aplikacja próbuje kilku, a własną instancję ustawisz przez `OVERPASS_URL`.

## Detektor

Domyślnie `MockDetector` (stałe wyniki, oznaczone jako dane przykładowe). Model wizyjny Claude:

```bash
pip install -e ".[vlm]"
export ANTHROPIC_API_KEY=...
export DETECTOR=claude
```

Zdjęcia z Mapillary: `bezbarier.sources.mapillary.images_near(lat, lon)` (wymaga `MAPILLARY_TOKEN`).
