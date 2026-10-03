# Wdrożenie i utrzymanie backendu

Materiał do prezentacji i dla podmiotów, które chcą uruchomić rozwiązanie u siebie.
Logika klasyfikacji: [klasyfikacja-barier.md](klasyfikacja-barier.md).

## Komponenty

```
                   ┌─────────── źródła danych (pobierane automatycznie) ───────────┐
                   │ OpenStreetMap (Nominatim, Overpass) · Mapillary · użytkownicy │
                   └───────────────────────────────┬───────────────────────────────┘
                                                   ▼
 frontend ──HTTPS──► API (FastAPI) ──► adaptery źródeł ──► obserwacje ──► SQLite
                          │                                                 │
                          └──► klasyfikator (profil × obserwacje) ◄─────────┘
                                         │
                          detektor zdjęć (Gemini Flash-Lite / Claude / mock)
```

- Pozyskiwanie danych (adaptery, detektor) jest oddzielone od prezentacji (klasyfikator, API).
- Miasto nie utrzymuje żadnej bazy: dane pobierają się same przy pierwszym wyświetleniu miejsca,
  a poprawki zgłaszają użytkownicy.
- Brak dostępu do systemów UMK/MJO - wyłącznie publiczne źródła.

## Uruchomienie (Docker)

W folderze `backend`:

```bash
docker build -t krakow-bez-barier .
docker run -d -p 8000:8000 --env-file .env -v bezbarier-data:/app/storage krakow-bez-barier
```

- `--env-file .env` - klucze (szablon: `.env.example`), nigdy nie wbudowywane w obraz.
- `-v bezbarier-data:/app/storage` - wolumen z bazą SQLite (głosy, poprawki, wyniki skanów).
- Obraz nie zależy od dostawcy chmury - działa na każdym hostingu kontenerów albo zwykłym VPS.

## Hosting i koszty

| Element | Opcja na start | Koszt |
|---|---|---|
| Serwer API | mały VPS albo hosting kontenerów z trwałym dyskiem (SQLite potrzebuje dysku) | rząd kilkudziesięciu zł / mies. |
| HTTPS | reverse proxy (np. Caddy) albo certyfikat od platformy hostingowej | 0 zł |
| OSM: Nominatim + Overpass | publiczne serwery (limity: ~1 zapytanie/s) | 0 zł; przy dużym ruchu własna instancja |
| Zdjęcia Mapillary | publiczne API z darmowym tokenem | 0 zł |
| Statystyki GUS (BDL) | publiczne API, opcjonalny klucz `GUS_CLIENT_ID` (wyższe limity) | 0 zł |
| Detekcja AI (Gemini Flash-Lite) | płatne za zdjęcie, **raz** - wynik jest zapisywany | liczba zdjęć × cena za zapytanie (aktualny cennik Google) |

Koszt AI nie rośnie z liczbą użytkowników, tylko z liczbą nowych zdjęć - to samo zdjęcie nigdy nie jest analizowane drugi raz.

## Odpowiedzialność

| Obszar | Co obejmuje |
|---|---|
| Hosting | serwer, kopie zapasowe pliku bazy (wystarczy kopiowanie jednego pliku) |
| Aktualizacje | nowe wersje kodu (obraz Docker), aktualizacje bibliotek |
| Bezpieczeństwo | HTTPS, klucze w zmiennych środowiskowych, `ADMIN_TOKEN` dla płatnego skanu, `CORS_ORIGINS` ograniczone do domeny frontendu |
| Obsługa zgłoszeń | przegląd zaprzeczeń i poprawek od użytkowników (`POST /observations/{id}/votes`) |
| Koszty | hosting + detekcja AI dla nowych zdjęć |

## Aktualność danych

- **OSM** - pobierane w tle (już przy wyszukiwaniu, równolegle z kilku serwerów Overpass; gdy żaden nie odpowie w 12 s -
  jedno małe zapytanie do głównego API OpenStreetMap, które służy głównie do edycji, więc tylko awaryjnie), ocena nie czeka -
  aplikacja dociąga wynik sama; po nieudanej próbie kolejna dopiero po 2 min. Odświeżane co 30 dni (`OSM_REFRESH_DAYS`); data informacji =
  `check_date` albo ostatnia edycja w OSM. Nieudane odświeżenie = komunikat w `warnings`, ocena na wcześniejszych danych.
- **Zdjęcia** - skan na żądanie (`/places/{id}/scan`); data informacji = data wykonania zdjęcia, nie analizy.
  Skan wybiera zdjęcia z ostatnich 3 lat (starsze tylko, gdy nowszych nie ma - z adnotacją w `notes`),
  a każdy kolejny skan tego samego miejsca analizuje następne, jeszcze nieprzeanalizowane zdjęcia.
- **Użytkownicy** - każde potwierdzenie odświeża datę informacji; zaprzeczenia obniżają wiarygodność.
- Niedostępne źródło nie blokuje aplikacji: odpowiedź zawiera ostrzeżenie w `warnings`, a ocena opiera się na danych już zapisanych.

## Ochrona danych

- **Brak kont i danych osobowych.** Nie pytamy o niepełnosprawność - profil to progi (np. maks. wysokość progu).
- **Profil nie jest zapisywany na serwerze** - frontend wysyła go przy każdym zapytaniu i może trzymać go tylko na urządzeniu.
- Przy głosach zapisujemy wyłącznie losowy identyfikator urządzenia (`voter_id`) i datę.
- Zdjęcia wysyłane do Gemini nie są przechowywane po stronie Google (`store=False`).
- Wszystkie klucze w zmiennych środowiskowych; `.env` jest wykluczony z repozytorium.
- Płatna analiza zdjęć tylko z nagłówkiem `X-Admin-Token`; głosy ograniczone do 30 na godzinę z jednego adresu.

## Nowe miasto

1. Ustaw `CITY_VIEWBOX` na obszar miasta (format `minLon,maxLat,maxLon,minLat`).
2. Gotowe - OSM i Mapillary są globalne, klasyfikator i profile nie zależą od miasta.
3. Opcjonalnie: adapter lokalnych otwartych danych w `backend/src/bezbarier/sources/` zwracający `Observation`.

## Zależności i licencje

| Zależność | Licencja / warunki | Obowiązek |
|---|---|---|
| Dane OpenStreetMap | ODbL | oznaczenie „© OpenStreetMap contributors” we frontendzie |
| Zdjęcia Mapillary | CC-BY-SA 4.0 | link do zdjęcia przy informacji (jest w `source.url`) |
| Nominatim / Overpass | zasady użycia publicznych instancji | własny User-Agent, umiarkowany ruch |
| Gemini API | warunki Google AI | - |
| FastAPI, Pydantic, httpx, python-dotenv, google-genai | MIT / BSD / Apache 2.0 | - |

## Znane ograniczenia prototypu

- Elementy usunięte z OSM zostają w bazie po odświeżeniu (odświeżenie nadpisuje, ale nie kasuje).
- Limit głosów jest per adres IP i w pamięci serwera - zdeterminowana osoba obejdzie go zmianą sieci;
  docelowo weryfikacja (np. CAPTCHA przy poprawkach) i moderacja zaprzeczeń.
- SQLite = jedna instancja serwera; przy skalowaniu poziomym PostgreSQL + PostGIS (ten sam interfejs repozytorium).
- Jedno wejście na miejsce; lokalizacja cech ze zdjęć = pozycja aparatu, nie obiektu.
- Progi w profilach i trafność detekcji AI wymagają walidacji z użytkownikami.
