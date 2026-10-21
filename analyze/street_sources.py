"""Free street-imagery discovery for shortlist verification.

This module deliberately does not use Google Street View or any paid endpoint.
It finds public imagery near a *small, already-ranked* shortlist.  The image
URLs are retained as evidence for a later visual-comparison pass; we do not
pretend that merely finding a nearby frame proves a match.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import quote_plus

import httpx

KARTAVIEW = "https://api.openstreetcam.org/2.0/photo/"
MAPILLARY = "https://graph.mapillary.com/images"
PANORAMAX = "https://panoramax.ign.fr/api/search"
UA = "WhereWas/1.0 (local visual place shortlist)"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(7.5, connect=3.0), headers={"User-Agent": UA})


def _photo_list(data: object) -> list[dict]:
    """Normalise the public KartaView response, which has changed shape."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("result", "data", "photos", "items"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
        if isinstance(rows, dict):
            for nested in ("data", "photos", "items"):
                if isinstance(rows.get(nested), list):
                    return [x for x in rows[nested] if isinstance(x, dict)]
    return []


async def kartaview_nearby(lat: float, lon: float, radius_m: int = 120) -> list[dict]:
    """Public KartaView imagery; no key required for normal read requests."""
    async with _client() as client:
        try:
            r = await client.get(KARTAVIEW, params={"lat": lat, "lng": lon, "radius": radius_m})
            if r.status_code != 200:
                return []
            rows = _photo_list(r.json())
        except Exception:
            return []
    out = []
    for row in rows[:8]:
        thumb = row.get("lth_name") or row.get("fileurl") or row.get("thumbnailUrl") or row.get("url")
        if thumb:
            out.append({"provider": "kartaview", "id": str(row.get("id") or row.get("photoId") or thumb), "thumbnail": str(thumb)})
    return out


async def mapillary_nearby(lat: float, lon: float, radius_m: int = 120) -> list[dict]:
    """Optional provider. It activates only after the owner supplies a token."""
    token = (os.environ.get("MAPILLARY_ACCESS_TOKEN") or "").strip()
    if not token:
        return []
    delta = radius_m / 111_000
    bbox = f"{lon-delta},{lat-delta},{lon+delta},{lat+delta}"
    async with _client() as client:
        try:
            r = await client.get(
                MAPILLARY,
                params={"access_token": token, "bbox": bbox, "limit": 8, "fields": "id,thumb_1024_url,computed_geometry"},
            )
            if r.status_code != 200:
                return []
            rows = r.json().get("data") or []
        except Exception:
            return []
    return [
        {"provider": "mapillary", "id": str(row.get("id")), "thumbnail": row.get("thumb_1024_url")}
        for row in rows if row.get("id") and row.get("thumb_1024_url")
    ]


async def panoramax_nearby(lat: float, lon: float, radius_m: int = 120) -> list[dict]:
    """Open Panoramax/STAC fallback. Empty coverage is a normal outcome."""
    delta = radius_m / 111_000
    bbox = f"{lon-delta},{lat-delta},{lon+delta},{lat+delta}"
    async with _client() as client:
        try:
            r = await client.get(PANORAMAX, params={"bbox": bbox, "limit": 8})
            if r.status_code != 200:
                return []
            features = r.json().get("features") or []
        except Exception:
            return []
    out = []
    for item in features[:8]:
        assets = item.get("assets") or {}
        preview = next((a.get("href") for a in assets.values() if isinstance(a, dict) and a.get("href")), None)
        if preview:
            out.append({"provider": "panoramax", "id": str(item.get("id") or preview), "thumbnail": preview})
    return out


async def attach_free_street_sources(candidates: list[dict]) -> None:
    """Attach availability to only the top shortlist, limiting public API use."""
    async def enrich(candidate: dict) -> None:
        lat, lon = candidate.get("lat"), candidate.get("lon")
        if lat is None or lon is None:
            return
        shots = await asyncio.gather(
            kartaview_nearby(float(lat), float(lon)),
            panoramax_nearby(float(lat), float(lon)),
            mapillary_nearby(float(lat), float(lon)),
            return_exceptions=True,
        )
        imagery = []
        for result in shots:
            if isinstance(result, list):
                imagery.extend(result)
        candidate["free_street_imagery"] = imagery[:12]
        candidate["free_street_sources"] = sorted({item["provider"] for item in imagery})
        # This link is a user-facing exploration fallback, not an API call.
        candidate["kartaview"] = f"https://kartaview.org/map/@{lat},{lon},18z"
    await asyncio.gather(*(enrich(c) for c in candidates[:4]))
