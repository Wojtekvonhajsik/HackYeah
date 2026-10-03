# Klasyfikacja barier wg profilu użytkownika

## Założenia (z kryteriów wyzwania)

1. **Profil = potrzeby, nie diagnoza.** Presety opisują potrzebę, a nie niepełnosprawność
   (np. „Bez stopni, tylko płaskie przejścia” zamiast „jeżdżę na wózku”) i są tylko wartościami startowymi progów.
   Klasyfikator widzi wyłącznie progi i preferencje.

   | Klucz | Etykieta |
   |---|---|
   | `step_free_strict` | Bez stopni, tylko płaskie przejścia |
   | `step_free` | Bez stopni, niskie progi OK |
   | `stroller` | Z wózkiem dziecięcym |
   | `limited_endurance` | Krótkie dystanse, potrzebuję poręczy |
   | `non_visual` | Orientacja bez wzroku |
2. **Nigdy "dostępne/niedostępne".** Werdykt per bariera + uzasadnienie. Brak danych ≠ dostępne.
3. **Źródło, data i wiarygodność przy każdej informacji.** Dane z AI i zgłoszeń są wyraźnie oznaczone jako niepotwierdzone.
4. **Zdjęcia: Mapillary (CC-BY-SA) / zdjęcia użytkowników**, nie Street View - warunki Google Maps Platform
   ograniczają tworzenie własnych danych na bazie ich treści.

## Przepływ danych

```
źródła zdjęć ──► DETEKCJA (bez profilu) ──► Observations ──► fuzja z OSM / danymi miasta / zgłoszeniami
                                                               │
                                       profil (potrzeby) ──► KLASYFIKACJA (w czasie zapytania) ──► Assessment
```

- Detekcja jest niezależna od profilu: jedno zdjęcie analizujemy raz, wynik służy wszystkim.
- Klasyfikacja to deterministyczne reguły (`classification/rules.py`) - wytłumaczalne i testowalne.
  Czysta funkcja bez I/O, więc może działać na telefonie (profil nie opuszcza urządzenia).

## Werdykty

| Werdykt | Znaczenie |
|---|---|
| `blocker` | dla Twoich ustawień nie do pokonania |
| `uncertain` | zakres pomiaru przecina Twój limit - może być blokadą, do sprawdzenia |
| `difficult` | da się, z trudem |
| `unknown` | brak danych - nigdy nie traktowany jak OK |
| `ok` | w normie |
| `amenity` | udogodnienie |

Progi mają dwa poziomy: `soft` (komfort) i `hard` (granica). Wymiary z AI to zakresy `{lo, hi}`.

## Wiarygodność

```
trust = waga_źródła × pewność_detektora × głosy × 0.5^(wiek / okres_półtrwania[typ])
głosy = (potwierdzenia + 1) / (potwierdzenia + zaprzeczenia + 1)
```

- Wagi: urzędowe 0.95, właściciel 0.85, zweryfikowane 0.8, OSM 0.7, zgłoszenie 0.5, AI 0.4.
- Okres półtrwania zależy od typu: krawężnik 2 lata, przeszkoda 90 dni, przeszkoda tymczasowa 14 dni.
  Wiek liczymy od daty obserwacji albo ostatniego potwierdzenia przez użytkownika (co nowsze).
  Dla OSM: tag `check_date`, a bez niego data ostatniej edycji elementu.
- Status: `confirmed` / `unverified` / `outdated` (starsze niż okres półtrwania) / `conflicting`.

## Potwierdzenia i poprawki od użytkowników

`POST /observations/{id}/votes` z `confirm` albo `deny` - jeden głos na urządzenie (losowy `voter_id`, bez kont).

- 3 potwierdzenia (i co najmniej 2× więcej niż zaprzeczeń) → informacja ma status `confirmed`.
- Każde potwierdzenie odświeża datę informacji; zaprzeczenia obniżają wiarygodność.
- Przy `deny` można podać `correction_attrs` (jak jest naprawdę) → nowa obserwacja typu „zgłoszenie użytkownika”,
  która trafia do tej samej cechy - jeśli kłóci się z oryginałem, użytkownik zobaczy konflikt.

## Źródła danych

| Źródło | Co daje | Licencja | Jak pobieramy |
|---|---|---|---|
| OpenStreetMap (Nominatim) | wyszukiwanie miejsc w Krakowie | ODbL | `GET /places/search`, na żądanie |
| OpenStreetMap (Overpass) | krawężniki, przejścia, wejścia, nawierzchnia, schody, ławki, toalety w promieniu 30 m | ODbL | przy pierwszej ocenie miejsca, potem z pamięci |
| Mapillary | zdjęcia ulic do detekcji AI | CC-BY-SA 4.0 | `sources/mapillary.py` |
| Zgłoszenia użytkowników | potwierdzenia, poprawki | - | `POST /observations/{id}/votes` |

Gdy źródło jest niedostępne, ocena nadal działa na danych zapisanych wcześniej, a odpowiedź zawiera
ostrzeżenie w `warnings` (np. „Nie udało się pobrać danych z OpenStreetMap...”).

Tag OSM `wheelchair=yes/limited/no` na obiekcie przeliczamy na przybliżoną wysokość progu (0-2 / 2-7 / 7-30 cm),
zgodnie z definicjami z wiki OSM - dzięki temu ta sama informacja daje różne werdykty dla różnych profili.

## Konflikty

Obserwacje tego samego typu w promieniu 4 m (wejścia: to samo `place_id`) to jedna cecha.
Jeśli źródła o porównywalnej wiarygodności (≥ 50% najlepszego) dają różne werdykty dla użytkownika,
cecha dostaje status `conflicting`, pokazujemy **ostrożniejszy** werdykt i wszystkie dowody.

## Pewność

Dla każdej cechy `confidence_pct` (0-100) - na ile dane potwierdzają werdykt:

```
za     = 1 - Π(1 - trust)  po źródłach zgodnych z werdyktem   (zgodne źródła wzmacniają się)
przeciw = 1 - Π(1 - trust)  po źródłach sprzecznych
pewność = za × (1 - przeciw)
```

- Jedno świeże źródło OSM → 70%, właściciel → 85%, świeża detekcja AI (pewność modelu 0,9) → 36%,
  dwie zgodne detekcje AI → 59%. Stare dane tracą pewność razem z wiarygodnością.
- „Do sprawdzenia” (`uncertain`) maks. 50%; brak danych → brak pewności.
- **Przeszkoda z pewnością < 30% jest pokazywana jako „do sprawdzenia”** - stare zdjęcie sprzed lat nie przesądza,
  że miejsce jest nie do pokonania, ale informacja nie znika.
- To wskaźnik orientacyjny (heurystyka na wagach źródeł), a nie skalibrowane prawdopodobieństwo -
  kalibracja wymaga porównania z weryfikacją w terenie.

## Miejsce i okolica

Cecha należy do **miejsca** (`scope=place`), gdy jest do niego przypisana: tagi obiektu w OSM, wejście w promieniu 15 m
(z jego szerokością i stopniami), wejście widoczne na zdjęciu z kamery skierowanej na miejsce, zgłoszenia przy miejscu.
Reszta w promieniu 30 m (i dalsze zdjęcia ze skanu) to **okolica** (`scope=surroundings`).

O nagłówku decyduje tylko miejsce - schody w okolicy dworca nie znaczą, że dworzec jest „nie do pokonania”.
Okolica jest podsumowana osobno („W okolicy: schody (przeszkoda) x7, winda. Sprawdź trasę dojścia.”).

## Podsumowanie miejsca / odcinka

`barriers` > `difficulties` > `incomplete_data` > `no_known_barriers` - liczone z cech miejsca.
`incomplete_data`, gdy brakuje kluczowej informacji (dla miejsca i wózka: wejście), któraś cecha miejsca jest `unknown`
albo o samym miejscu nie ma żadnych danych. `no_known_barriers` zawsze z dopiskiem, że to nie gwarancja dostępności.

Pewność nagłówka (`summary_confidence_pct`): przy przeszkodach/utrudnieniach - najpewniejsza z nich;
przy „brak znanych barier” - najsłabiej potwierdzona informacja o miejscu.

## Jak rozszerzać

- **Nowy typ cechy:** `FeatureType` w `models.py` + reguła w `RULES` (`rules.py`) + okres półtrwania w `trust.py`
  + opis w prompcie detektora.
- **Nowe źródło:** adapter w `sources/` zwracający `Observation` z odpowiednim `SourceType`.
- **Nowe miasto:** nic w klasyfikatorze - tylko źródła danych dla nowego obszaru.
