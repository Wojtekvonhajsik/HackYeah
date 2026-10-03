# Backend - Kraków bez barier

Klasyfikacja barier wg potrzeb użytkownika. Projekt: [docs/klasyfikacja-barier.md](../docs/klasyfikacja-barier.md),
wdrożenie (Docker, koszty, ochrona danych): [docs/wdrozenie.md](../docs/wdrozenie.md),
instrukcja dla frontendu: [docs/api-frontend.md](../docs/api-frontend.md).

## Struktura

```
src/bezbarier/
  classification/   # rdzeń: model danych, profile, reguły, wiarygodność, fuzja, klasyfikator (bez I/O)
  detection/        # detektory zdjęć: MockDetector, GeminiVisionDetector, ClaudeVisionDetector
  scan.py           # skan okolicy miejsca: zdjęcia Mapillary -> detektor -> obserwacje
  sources/          # adaptery źródeł: OpenStreetMap (Nominatim + Overpass), Mapillary
  storage.py        # repozytorium: SQLite (backend/data/bezbarier.db) albo w pamięci w testach
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

## Aplikacja webowa (telefon)

Backend serwuje aplikację pod `http://localhost:8000/` (przekierowanie na `/app/`, pliki w `backend/web/`, bez kroku
budowania). Projektowana najpierw pod telefon, z obsługą klawiatury i czytnika ekranu (WCAG 2.2 AA).

Na telefonie w tej samej sieci Wi-Fi:

```bash
.venv\Scripts\python -m uvicorn bezbarier.api.main:app --host 0.0.0.0 --port 8000
```

i na telefonie otwórz `http://<adres IP komputera>:8000` (adres: `ipconfig`, pole „IPv4”). Windows może zapytać
o dostęp przez zaporę - zezwól dla sieci prywatnej. W Chrome na Androidzie: menu → „Dodaj do ekranu głównego”.

## Główne endpointy

| Endpoint | Co robi |
|---|---|
| `GET /presets` | profile potrzeb z etykietami dla użytkownika |
| `GET /places/search?q=...` | wyszukiwanie miejsc w Krakowie (OSM Nominatim) |
| `POST /places/{id}/assessment` | ocena miejsca dla profilu; przy pierwszym wywołaniu pobiera cechy z OSM (Overpass) |
| `POST /places/{id}/scan` | analiza zdjęć Mapillary wokół miejsca (każde zdjęcie tylko raz; z `ADMIN_TOKEN` wymaga nagłówka `X-Admin-Token`) |
| `POST /observations/{id}/votes` | potwierdzenie / zaprzeczenie informacji, opcjonalnie z poprawką |
| `POST /classify` | czysta klasyfikacja przesłanych obserwacji |
| `POST /observations/analyze` | detekcja cech na zdjęciu |

Frontend z innego portu: CORS jest domyślnie otwarty, na produkcji ustaw `CORS_ORIGINS`.
Publiczne serwery Overpass bywają przeciążone - aplikacja próbuje kilku, a własną instancję ustawisz przez `OVERPASS_URL`.

## Klucze i detektor

Skopiuj `.env.example` jako `.env` (w folderze `backend`) i uzupełnij. `.env` jest w `.gitignore`.

| Zmienna | Do czego |
|---|---|
| `MAPILLARY_TOKEN` | zdjęcia do `/places/{id}/scan` |
| `DETECTOR` | `mock` (domyślnie, stałe wyniki oznaczone jako przykładowe), `gemini` albo `claude` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | detektor Gemini (domyślnie `gemini-3.5-flash-lite`), wymaga `pip install -e ".[gemini]"` |
| `ANTHROPIC_API_KEY` | detektor Claude, wymaga `pip install -e ".[claude]"` |

Testy zawsze używają detektora `mock`, niezależnie od `.env`.

## Baza danych

Głosy, poprawki, dane pobrane z OSM i wyniki skanów zapisują się w `backend/data/bezbarier.db` (SQLite, plik w `.gitignore`)
i przetrwają restart serwera. Żeby zacząć od zera, zatrzymaj serwer i usuń ten plik.

## Przygotowanie demo

Przed prezentacją, na sprawdzonej sieci i przy zatrzymanym serwerze:

```bash
.venv\Scripts\python scripts\prepare_demo.py
```

Skrypt wyszukuje kilka miejsc w Krakowie, pobiera dla nich dane z OSM i analizuje po 3 zdjęcia
(`--max-images`, `--no-scan`, własne nazwy miejsc jako argumenty). Wszystko trafia do bazy, więc na demo
dane są dostępne nawet przy problemach z siecią. Na końcu wypisuje id miejsc.
