# Backend - Kraków bez barier

Klasyfikacja barier wg potrzeb użytkownika. Projekt: [docs/klasyfikacja-barier.md](../docs/klasyfikacja-barier.md).

## Struktura

```
src/bezbarier/
  classification/   # rdzeń: model danych, profile, reguły, wiarygodność, fuzja, klasyfikator (bez I/O)
  detection/        # detektory zdjęć: MockDetector, ClaudeVisionDetector
  sources/          # adaptery źródeł (Mapillary; dalej OSM, otwarte dane Krakowa)
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
curl -X POST localhost:8000/places/kawiarnia-rynek/assessment -H "Content-Type: application/json" -d '{"preset": "wheelchair_manual"}'
```

## Detektor

Domyślnie `MockDetector` (stałe wyniki, oznaczone jako dane przykładowe). Model wizyjny Claude:

```bash
pip install -e ".[vlm]"
export ANTHROPIC_API_KEY=...
export DETECTOR=claude
```

Zdjęcia z Mapillary: `bezbarier.sources.mapillary.images_near(lat, lon)` (wymaga `MAPILLARY_TOKEN`).
