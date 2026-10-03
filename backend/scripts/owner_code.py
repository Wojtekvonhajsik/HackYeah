"""Wydaje kod właściciela obiektu (hotel, muzeum...), którym może on uzupełnić dane o swoim miejscu.

Uruchom przy ZATRZYMANYM serwerze (serwer wczytuje bazę przy starcie). W folderze backend:

    .venv\\Scripts\\python scripts\\owner_code.py "Teatr Bagatela" "Teatr Bagatela - administracja"
    .venv\\Scripts\\python scripts\\owner_code.py osm-way-125207200 "Teatr Bagatela - administracja"

Kod pokazuje się tylko raz (w bazie jest wyłącznie jego hash) - przekaż go właścicielowi razem z linkiem.
"""

from __future__ import annotations

import argparse
import os
import sys

from fastapi.testclient import TestClient

from bezbarier.api import main as api


def cli() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Kod właściciela obiektu")
    parser.add_argument("place", help="id miejsca (np. osm-way-125207200) albo nazwa do wyszukania")
    parser.add_argument("owner_name", help="nazwa właściciela widoczna przy jego danych")
    parser.add_argument("--base-url", default="http://localhost:8000", help="adres aplikacji (np. adres tunelu)")
    args = parser.parse_args()

    client = TestClient(api.app)
    place_id = args.place
    if not place_id.startswith("osm-") and place_id not in api.repo.places:
        found = client.get("/places/search", params={"q": args.place})
        if found.status_code != 200 or not found.json():
            print(f"Nie znaleziono miejsca: {args.place}")
            return 1
        place_id = found.json()[0]["id"]

    headers = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]} if os.environ.get("ADMIN_TOKEN") else {}
    resp = client.post("/admin/owner-codes", json={"place_id": place_id, "owner_name": args.owner_name}, headers=headers)
    if resp.status_code != 200:
        print(f"Błąd: {resp.status_code} {resp.json().get('detail')}")
        return 1
    issued = resp.json()
    print(f"Miejsce:    {issued['place_name']} ({issued['place_id']})")
    print(f"Właściciel: {issued['owner_name']}")
    print(f"Kod:        {issued['code']}")
    print(f"Formularz:  {args.base_url.rstrip('/')}{issued['form_path']}")
    print("\nKod nie będzie widoczny ponownie - zapisz go teraz. Uruchom (albo zrestartuj) serwer.")
    return 0


if __name__ == "__main__":
    sys.exit(cli())
