"""Źródło zdjęć: Mapillary (licencja CC-BY-SA 4.0, wymaga darmowego tokenu).

Zamiast Street View, bo warunki Google Maps Platform ograniczają tworzenie własnych
danych na podstawie ich treści. Token: https://www.mapillary.com/dashboard/developers
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone

import httpx

from ..classification.models import GeoPoint
from ..detection.base import ImageRef

GRAPH_URL = "https://graph.mapillary.com/images"
FIELDS = "id,captured_at,compass_angle,computed_geometry,geometry,thumb_1024_url"
LICENSE = "CC-BY-SA 4.0"


def images_near(lat: float, lon: float, radius_m: float = 30, limit: int = 20, token: str | None = None) -> list[ImageRef]:
    token = token or os.environ.get("MAPILLARY_TOKEN")
    if not token:  # brak albo pusta wartość (np. "MAPILLARY_TOKEN=" z .env.example)
        raise KeyError("MAPILLARY_TOKEN")
    d_lat = radius_m / 111_320
    d_lon = radius_m / (111_320 * math.cos(math.radians(lat)))
    resp = httpx.get(
        GRAPH_URL,
        params={
            "access_token": token,
            "fields": FIELDS,
            "bbox": f"{lon - d_lon},{lat - d_lat},{lon + d_lon},{lat + d_lat}",
            "limit": limit,
        },
        timeout=20,
    )
    resp.raise_for_status()

    images = []
    for item in resp.json().get("data", []):
        geom = item.get("computed_geometry") or item.get("geometry")
        if not geom:
            continue
        img_lon, img_lat = geom["coordinates"]
        images.append(ImageRef(
            id=str(item["id"]),
            provider="mapillary",
            url=item.get("thumb_1024_url"),
            location=GeoPoint(lat=img_lat, lon=img_lon),
            heading=item.get("compass_angle"),
            captured_at=datetime.fromtimestamp(item["captured_at"] / 1000, tz=timezone.utc).date(),
            license=LICENSE,
            attribution_url=f"https://www.mapillary.com/app/?pKey={item['id']}",
        ))
    return images
