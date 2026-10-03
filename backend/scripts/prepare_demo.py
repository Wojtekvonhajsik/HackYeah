"""Przygotowanie demo: wyszukuje miejsca, pobiera dane z OSM i skanuje zdjęcia - wszystko trafia do bazy.

Uruchom PRZED prezentacją, na sprawdzonej sieci, przy ZATRZYMANYM serwerze
(serwer wczytuje bazę przy starcie i nie zobaczy danych dopisanych w trakcie działania).
W folderze backend:

    .venv\\Scripts\\python scripts\\prepare_demo.py
    .venv\\Scripts\\python scripts\\prepare_demo.py --max-images 5
    .venv\\Scripts\\python scripts\\prepare_demo.py --no-scan "Sukiennice" "Brama Floriańska"

Klucze i ustawienia (DETECTOR, GEMINI_API_KEY, MAPILLARY_TOKEN, ADMIN_TOKEN) z backend/.env.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from fastapi.testclient import TestClient

from bezbarier.api import main as api

# Różne typy miejsc - różne wyniki na demo (plac, brama z bruku, teatr, muzeum, galeria, dworzec)
DEMO_PLACES = [
    "Sukiennice",
    "Brama Floriańska",
    "Teatr im. Juliusza Słowackiego",
    "Muzeum Narodowe w Krakowie",
    "Galeria Krakowska",
    "Kraków Główny",
]


def run(queries: list[str], preset: str, max_images: int, scan: bool) -> int:
    client = TestClient(api.app)
    headers = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]} if os.environ.get("ADMIN_TOKEN") else {}
    print(f"Baza: {api.DATABASE_PATH}")
    print(f"Detektor: {os.environ.get('DETECTOR', 'mock')}, profil do podglądu: {preset}\n")
    prepared: list[tuple[str, str, str]] = []
    failures = 0

    for query in queries:
        print(f"== {query}")
        resp = client.get("/places/search", params={"q": query})
        time.sleep(1.1)  # zasady Nominatim: maks. ~1 zapytanie na sekundę
        if resp.status_code != 200 or not resp.json():
            print(f"   nie znaleziono ({resp.status_code}) - pomijam\n")
            failures += 1
            continue
        place = resp.json()[0]
        place_id = place["id"]
        print(f"   {place['name']} -> {place_id}")

        assessment = client.post(f"/places/{place_id}/assessment", params={"wait": "true"}, json={"preset": preset}).json()
        osm_count = sum(1 for f in assessment["features"] for e in f["evidence"] if e["source"]["type"] == "osm")
        print(f"   OSM: {osm_count} informacji" + _warnings(assessment))

        if scan:
            resp = client.post(f"/places/{place_id}/scan", params={"max_images": max_images}, headers=headers)
            if resp.status_code != 200:
                print(f"   skan nieudany: {resp.status_code} {resp.json().get('detail')}")
                failures += 1
            else:
                result = resp.json()
                print(
                    f"   zdjęcia: znaleziono {result['images_found']} (promień {result['radius_m']} m), "
                    f"przeanalizowano {result['images_analyzed']}, wykryto {len(result['observations'])} cech"
                )
                for note in result["notes"]:
                    print(f"   uwaga: {note}")
                for error in result["errors"]:
                    print(f"   BŁĄD: {error}")
                    failures += 1
            assessment = client.post(f"/places/{place_id}/assessment", params={"wait": "true"}, json={"preset": preset}).json()

        time.sleep(3)  # odstęp między miejscami - publiczny Overpass ma limit zapytań
        counts = ", ".join(f"{verdict}: {n}" for verdict, n in assessment["counts"].items() if n)
        print(f"   ocena: {assessment['summary']} ({counts or 'brak cech'})\n")
        prepared.append((place_id, place["name"], assessment["summary"]))

    print("Gotowe miejsca (id do frontendu / demo):")
    for place_id, name, summary in prepared:
        print(f"   {place_id:22} {summary:18} {name}")
    print("\nTeraz uruchom (albo zrestartuj) serwer, żeby wczytał nowe dane z bazy.")
    return 1 if failures else 0


def _warnings(assessment: dict) -> str:
    return "".join(f"\n   OSTRZEŻENIE: {w}" for w in assessment["warnings"])


def cli() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # polskie znaki także przy przekierowaniu do pliku
    parser = argparse.ArgumentParser(description="Przygotowanie danych do demo")
    parser.add_argument("queries", nargs="*", help=f"nazwy miejsc (domyślnie: {', '.join(DEMO_PLACES)})")
    parser.add_argument("--preset", default="step_free_strict", help="profil do podglądu ocen")
    parser.add_argument("--max-images", type=int, default=3, help="zdjęć na miejsce (każde to płatne zapytanie)")
    parser.add_argument("--no-scan", action="store_true", help="tylko dane z OSM, bez analizy zdjęć")
    args = parser.parse_args()
    return run(args.queries or DEMO_PLACES, args.preset, args.max_images, scan=not args.no_scan)


if __name__ == "__main__":
    sys.exit(cli())
