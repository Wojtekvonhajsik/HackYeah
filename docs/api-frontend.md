# API dla frontendu

Interaktywna dokumentacja z możliwością wywołań: `http://<adres-backendu>:8000/docs`.
CORS jest otwarty, więc frontend może działać na innym porcie. Dla telefonu/emulatora uruchom backend z
`--host 0.0.0.0` i podaj adres komputera w sieci (klient Expo: `EXPO_PUBLIC_API_URL`).

## Główny scenariusz

```
1. GET  /presets                       -> wybór profilu (raz, zapamiętaj na urządzeniu)
2. GET  /places/search?q=Sukiennice    -> lista miejsc
3. POST /places/{id}/assessment        -> ocena miejsca dla profilu
4. POST /observations/{id}/votes       -> "to prawda" / "to nieprawda" przy informacji
```

### 1. Profil - `GET /presets`

```json
{
  "step_free_strict": {"label": "Bez stopni, tylko płaskie przejścia", "description": "Każdy stopień to...", "needs": {...}},
  "stroller": {"label": "Z wózkiem dziecięcym", ...},
  ...
}
```

- Pokaż `label` + `description`. **Nie pytaj o niepełnosprawność** - to wymóg z kryteriów.
- Zapisz wybrany klucz (np. `"stroller"`) w pamięci urządzenia. Serwer nie przechowuje profilu - wysyłasz go przy każdej ocenie.
- Zaawansowane: użytkownik może zmienić progi - wyślij `overrides`, np.
  `{"preset": "step_free_strict", "overrides": {"mobility": {"max_edge_height_cm": {"soft": 3, "hard": 5}}}}`.

### 2. Wyszukiwanie - `GET /places/search?q=...`

```json
[{"id": "osm-way-23256528", "name": "Sukiennice", "address": "Sukiennice, 3, Rynek Główny, ...",
  "location": {"lat": 50.0617, "lon": 19.9373}, ...}]
```

`id` jest stałe - można go zapisać (ulubione, historia) i użyć później; działa także po restarcie serwera,
bo miejsca `osm-*` backend odtwarza sam, a dane trzyma w bazie.

### 3. Ocena - `POST /places/{id}/assessment`

Body: `{"preset": "step_free_strict"}`. **Pierwsze wywołanie dla miejsca może trwać do ~30 s** (pobieranie z OpenStreetMap) - pokaż wskaźnik ładowania.

| Pole | Co z nim zrobić |
|---|---|
| `summary` | `barriers` / `difficulties` / `incomplete_data` / `no_known_barriers` - nagłówek oceny |
| `summary_text` | gotowe zdanie do nagłówka |
| `text` | **cała ocena jako tekst** - dla czytnika ekranu (np. region `aria-live`), asystenta głosowego (`speechSynthesis`) i jako tekstowa alternatywa dla mapy |
| `features[]` | lista barier i udogodnień, od najpoważniejszych |
| `missing[]` | czego nie wiemy - pokaż wyraźnie |
| `warnings[]` | np. niedostępne źródło danych - pokaż jako komunikat |
| `contains_sample_data` | `true` -> baner „dane przykładowe” |
| `contains_unverified` | `true` -> informacja, że część danych jest niepotwierdzona |

**Zasada: nigdy nie wyświetlaj „dostępne”.** `no_known_barriers` = „Nie znaleźliśmy barier w dostępnych danych” (tekst jest w `summary_text`).

Każdy element `features[]`:

| Pole | Opis |
|---|---|
| `label`, `type` | np. „krawężnik” / `kerb` |
| `verdict` | `blocker` / `uncertain` / `difficult` / `unknown` / `ok` / `amenity` |
| `reasons[]` | uzasadnienie po polsku, np. „krawężnik 8-15 cm - powyżej Twojego limitu 4 cm” |
| `status` | `confirmed` / `unverified` / `outdated` / `conflicting` |
| `location` | punkt na mapę |
| `primary_observation_id` | obserwacja, z której pochodzi werdykt |
| `evidence[]` | wszystkie źródła: `source.name`, `source.url`, `source.license`, `source.observed_at` (data informacji), `note`, `confirmations`, `denials`, `observation_id` (do głosowania) |

Werdykt pokazuj **tekstem i ikoną, nie samym kolorem** (WCAG). Proponowane etykiety:

| `verdict` | Etykieta | `status` | Etykieta |
|---|---|---|---|
| `blocker` | Przeszkoda | `confirmed` | Potwierdzone |
| `uncertain` | Do sprawdzenia | `unverified` | Niepotwierdzone (np. wykryte automatycznie) |
| `difficult` | Utrudnienie | `outdated` | Nieaktualne |
| `unknown` | Brak danych | `conflicting` | Źródła się nie zgadzają |
| `ok` | Bez przeszkód | | |
| `amenity` | Udogodnienie | | |

### 4. Głosy i poprawki - `POST /observations/{observation_id}/votes`

```json
{"voter_id": "<losowy id urządzenia>", "value": "confirm"}
{"voter_id": "<losowy id urządzenia>", "value": "deny", "correction_attrs": {"kind": "lowered"}}
```

- `voter_id`: wygeneruj raz losowy UUID i trzymaj na urządzeniu (tak robi już `getVoterId()` w `frontend/src/api/client.ts`) - bez kont i danych osobowych.
- `observation_id` bierzesz z `features[].evidence[]`.
- Po głosie pobierz ocenę ponownie. Limit: 30 głosów na godzinę z jednego adresu (odpowiedź `429` + nagłówek `Retry-After`).

## Przykład (fetch)

```js
const API = "http://localhost:8000";
const preset = localStorage.getItem("preset") ?? "step_free_strict";

const places = await fetch(`${API}/places/search?q=${encodeURIComponent("Sukiennice")}`).then(r => r.json());
const assessment = await fetch(`${API}/places/${places[0].id}/assessment`, {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({preset}),
}).then(r => r.json());

document.querySelector("#summary").textContent = assessment.summary_text;
document.querySelector("#text-version").textContent = assessment.text;  // alternatywa dla mapy
```

## Wymagane oznaczenia źródeł

- Mapa i dane OSM: „© OpenStreetMap contributors” z linkiem do https://www.openstreetmap.org/copyright.
- Informacje ze zdjęć: link `source.url` do zdjęcia w Mapillary + licencja `source.license` (CC-BY-SA 4.0).

## Błędy

| Kod | Kiedy |
|---|---|
| `400` | brak profilu, nieznany preset, poprawka bez `deny` |
| `404` | nieznane miejsce lub obserwacja |
| `429` | za dużo głosów |
| `503` | wyszukiwarka OSM niedostępna |

Treść błędu jest zawsze w `detail` (po polsku).

## Tylko dla administratora

`POST /places/{id}/scan` i `POST /observations/analyze` analizują zdjęcia płatnym modelem AI i wymagają
nagłówka `X-Admin-Token`. Nie wywołuj ich z frontendu dla zwykłych użytkowników - dane do demo przygotowuje
skrypt `backend/scripts/prepare_demo.py`.
