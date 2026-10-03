"""Kampanie reklamowe w asystencie: tworzenie i raport wyświetleń/kliknięć.

Warunek reklamy: właściciel potwierdził dane o dostępności miejsca (scripts/owner_code.py + formularz).
Uruchom przy ZATRZYMANYM serwerze. W folderze backend:

    .venv\\Scripts\\python scripts\\sponsor.py "Kawiarnia X" "Kawiarnia X sp. z o.o." "Wejście bez progu, toaleta dla wózków" --days 30
    .venv\\Scripts\\python scripts\\sponsor.py --report
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
    parser = argparse.ArgumentParser(description="Kampanie reklamowe miejsc")
    parser.add_argument("place", nargs="?", help="id miejsca albo nazwa do wyszukania")
    parser.add_argument("sponsor_name", nargs="?", help="reklamodawca")
    parser.add_argument("tagline", nargs="?", help="tekst reklamy (do 120 znaków)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--report", action="store_true", help="pokaż kampanie i ich statystyki")
    args = parser.parse_args()

    client = TestClient(api.app)
    headers = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]} if os.environ.get("ADMIN_TOKEN") else {}

    if args.report:
        for s in client.get("/admin/sponsorships", headers=headers).json():
            place = api.repo.places.get(s["place_id"])
            ctr = f"{100 * s['clicks'] / s['impressions']:.0f}%" if s["impressions"] else "-"
            print(f"{s['starts']}..{s['ends']}  {place.name if place else s['place_id']:35} {s['sponsor_name']:25} "
                  f"wyświetlenia {s['impressions']:4}  kliknięcia {s['clicks']:4}  CTR {ctr}")
        return 0
    if not (args.place and args.sponsor_name and args.tagline):
        parser.error("podaj miejsce, reklamodawcę i tekst reklamy (albo --report)")

    place_id = args.place
    if not place_id.startswith("osm-") and place_id not in api.repo.places:
        found = client.get("/places/search", params={"q": args.place})
        if found.status_code != 200 or not found.json():
            print(f"Nie znaleziono miejsca: {args.place}")
            return 1
        place_id = found.json()[0]["id"]

    resp = client.post("/admin/sponsorships", headers=headers, json={
        "place_id": place_id, "sponsor_name": args.sponsor_name, "tagline": args.tagline, "days": args.days,
    })
    if resp.status_code != 200:
        print(f"Błąd: {resp.status_code} {resp.json().get('detail')}")
        return 1
    s = resp.json()
    print(f"Kampania {s['id']}: {s['sponsor_name']} - „{s['tagline']}” ({s['starts']} - {s['ends']})")
    print("Uruchom (albo zrestartuj) serwer.")
    return 0


if __name__ == "__main__":
    sys.exit(cli())
