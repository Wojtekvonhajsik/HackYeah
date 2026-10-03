# Klasyfikacja barier wg profilu użytkownika

## Założenia (z kryteriów wyzwania)

1. **Profil = potrzeby, nie diagnoza.** Presety (`wheelchair_manual`, `stroller`, `blind`...) to tylko
   wartości startowe progów. Klasyfikator widzi wyłącznie progi i preferencje.
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
trust = waga_źródła × pewność_detektora × 0.5^(wiek / okres_półtrwania[typ])
```

- Wagi: urzędowe 0.95, właściciel 0.85, zweryfikowane 0.8, OSM 0.7, zgłoszenie 0.5, AI 0.4.
- Okres półtrwania zależy od typu: krawężnik 2 lata, przeszkoda 90 dni, przeszkoda tymczasowa 14 dni.
- Status: `confirmed` / `unverified` / `outdated` (starsze niż okres półtrwania) / `conflicting`.

## Konflikty

Obserwacje tego samego typu w promieniu 4 m (wejścia: to samo `place_id`) to jedna cecha.
Jeśli źródła o porównywalnej wiarygodności (≥ 50% najlepszego) dają różne werdykty dla użytkownika,
cecha dostaje status `conflicting`, pokazujemy **ostrożniejszy** werdykt i wszystkie dowody.

## Podsumowanie miejsca / odcinka

`barriers` > `difficulties` > `incomplete_data` > `no_known_barriers`.
`incomplete_data`, gdy brakuje kluczowej informacji (dla miejsca i wózka: wejście) albo któraś cecha jest `unknown`.
`no_known_barriers` zawsze z dopiskiem, że to nie gwarancja dostępności.

## Jak rozszerzać

- **Nowy typ cechy:** `FeatureType` w `models.py` + reguła w `RULES` (`rules.py`) + okres półtrwania w `trust.py`
  + opis w prompcie detektora.
- **Nowe źródło:** adapter w `sources/` zwracający `Observation` z odpowiednim `SourceType`.
- **Nowe miasto:** nic w klasyfikatorze - tylko źródła danych dla nowego obszaru.
