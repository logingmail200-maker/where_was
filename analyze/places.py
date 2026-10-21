from __future__ import annotations

import asyncio
import re

import httpx

UA = "WhereWas/1.0 (local photo geolocation; https://github.com/local/wherewas)"
NOMINATIM = "https://nominatim.openstreetmap.org"
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.openstreetmap.fr/api/interpreter",
]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(18.0, connect=6.0),
        headers={"User-Agent": UA, "Accept": "application/json"},
        follow_redirects=True,
    )


async def reverse_geocode(lat: float, lon: float) -> dict | None:
    async with _client() as client:
        r = await client.get(
            f"{NOMINATIM}/reverse",
            params={"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1},
        )
        if r.status_code != 200:
            return None
        return r.json()


async def search_places(queries: list[str], region_hint: str | None = None, max_queries: int = 6) -> list[dict]:
    """Nominatim search — few queries, 1.1s apart (usage policy)."""
    out, seen = [], set()
    uniq = []
    for q in queries:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if len(q) < 4 or q.lower() in {x.lower() for x in uniq}:
            continue
        uniq.append(q)
        if len(uniq) >= max_queries:
            break
    async with _client() as client:
        for i, q in enumerate(uniq):
            if i:
                await asyncio.sleep(1.05)
            qq = f"{q} {region_hint}" if region_hint else q
            try:
                r = await client.get(
                    f"{NOMINATIM}/search",
                    params={"q": qq, "format": "jsonv2", "addressdetails": 1, "limit": 3},
                )
                if r.status_code != 200:
                    continue
                for hit in r.json():
                    pid = hit.get("place_id")
                    if pid in seen:
                        continue
                    seen.add(pid)
                    lat, lon = float(hit["lat"]), float(hit["lon"])
                    out.append(
                        {
                            "source": "nominatim",
                            "query": q,
                            "name": hit.get("name") or hit.get("display_name", "").split(",")[0],
                            "address": hit.get("display_name"),
                            "lat": lat,
                            "lon": lon,
                            "osm_type": hit.get("osm_type"),
                            "osm_id": hit.get("osm_id"),
                            "category": f"{hit.get('category') or ''}:{hit.get('type') or ''}",
                            "importance": hit.get("importance") or 0,
                        }
                    )
            except Exception:
                continue
    return out


async def overpass_named(names: list[str], bbox: tuple[float, float, float, float] | None = None) -> list[dict]:
    """Find mapped features matching names. bbox = (s, w, n, e)."""
    names = [re.sub(r"[^\w\s]", " ", n).strip() for n in names if n and len(n) >= 4][:3]
    if not names:
        return []
    area = ""
    if bbox:
        s, w, n, e = bbox
        area = f"({s},{w},{n},{e})"
    parts = []
    for n in names:
        pat = re.escape(n.split()[0] if len(n) > 40 else n)
        if len(pat) < 4:
            continue
        parts.append(f'nwr["name"~"{pat}",i]{area};nwr["brand"~"{pat}",i]{area};')
    if not parts:
        return []
    query = f"[out:json][timeout:22];({''.join(parts)});out center tags 40;"
    data = None
    async with _client() as client:
        for url in OVERPASS:
            try:
                r = await client.post(url, data={"data": query})
                if r.status_code == 200:
                    data = r.json()
                    break
            except Exception:
                continue
    if not data:
        return []
    hits = []
    for e in data.get("elements") or []:
        tags = e.get("tags") or {}
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        label = tags.get("name") or tags.get("brand") or tags.get("operator")
        if not label:
            continue
        hits.append(
            {
                "source": "overpass",
                "name": label,
                "address": ", ".join(
                    filter(
                        None,
                        [
                            tags.get("addr:street"),
                            tags.get("addr:suburb"),
                            tags.get("addr:city"),
                            tags.get("addr:state"),
                        ],
                    )
                )
                or None,
                "lat": float(lat),
                "lon": float(lon),
                "category": tags.get("amenity") or tags.get("shop") or tags.get("tourism") or "",
                "osm_type": e.get("type"),
                "osm_id": e.get("id"),
            }
        )
    return hits[:40]


async def search_near(query: str, lat: float, lon: float, km: float = 0.5) -> list[dict]:
    """Nominatim bounded to a small box — used as Overpass fallback."""
    s, w, n, e = bbox_around(lat, lon, km)
    viewbox = f"{w},{n},{e},{s}"
    async with _client() as client:
        try:
            r = await client.get(
                f"{NOMINATIM}/search",
                params={
                    "q": query,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": 5,
                    "viewbox": viewbox,
                    "bounded": 1,
                },
            )
            if r.status_code != 200:
                return []
            out = []
            for hit in r.json():
                out.append(
                    {
                        "name": hit.get("name") or hit.get("display_name", "").split(",")[0],
                        "address": hit.get("display_name"),
                        "lat": float(hit["lat"]),
                        "lon": float(hit["lon"]),
                        "osm_id": hit.get("osm_id"),
                    }
                )
            return out
        except Exception:
            return []


def bbox_around(lat: float, lon: float, km: float = 40) -> tuple[float, float, float, float]:
    dlat = km / 111.0
    dlon = km / max(20.0, 111.0 * max(0.2, abs(__import__("math").cos(__import__("math").radians(lat)))))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


async def nearby_elevated(points: list[dict], radius_m: int = 350) -> list[dict]:
    """Bridges / flyovers / metro near candidate points — second-clue check."""
    pts = [p for p in points if p.get("lat") is not None][:12]
    if not pts:
        return []
    bits = []
    for p in pts:
        lat, lon = p["lat"], p["lon"]
        bits.append(
            f'way(around:{radius_m},{lat},{lon})["highway"~"^(primary|secondary|tertiary|trunk|motorway)$"]["bridge"];'
            f'way(around:{radius_m},{lat},{lon})["bridge"];'
            f'way(around:{radius_m},{lat},{lon})["name"~"flyover|viaduct|elevated",i];'
            f'way(around:{radius_m},{lat},{lon})["railway"]["bridge"];'
            f'node(around:{radius_m},{lat},{lon})["bridge:support"];'
        )
    query = f"[out:json][timeout:20];({''.join(bits)});out center tags 30;"
    data = None
    async with _client() as client:
        for url in OVERPASS:
            try:
                r = await client.post(url, data={"data": query})
                if r.status_code == 200:
                    data = r.json()
                    break
            except Exception:
                continue
    if not data:
        return []
    feats = []
    for e in data.get("elements") or []:
        tags = e.get("tags") or {}
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None:
            continue
        name = tags.get("name") or tags.get("highway") or tags.get("railway") or "elevated"
        name_l = name.lower()
        is_elevated = bool(
            tags.get("bridge") not in (None, "no")
            or tags.get("bridge:support")
            or tags.get("layer") not in (None, "0", 0)
            or any(x in name_l for x in ("flyover", "viaduct", "elevated", "bridge"))
        )
        if not is_elevated:
            continue
        feats.append(
            {
                "lat": float(lat),
                "lon": float(lon),
                "name": name,
                "tags": tags,
                "is_elevated": True,
            }
        )
    return feats[:40]


def pair_names(hits: list[dict], max_m: float = 200) -> list[dict]:
    """Hits from two different search queries that sit near each other."""
    import math

    def dist(a, b):
        r = 6371000.0
        p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
        dp = math.radians(b["lat"] - a["lat"])
        dl = math.radians(b["lon"] - a["lon"])
        x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.atan2(math.sqrt(x), math.sqrt(1 - x))

    pairs = []
    for i, a in enumerate(hits):
        for b in hits[i + 1 :]:
            qa, qb = (a.get("query") or a.get("name") or "").lower(), (b.get("query") or b.get("name") or "").lower()
            if not qa or qa == qb:
                continue
            na = re.sub(r"[^a-z0-9]+", "", (a.get("name") or "").lower())
            nb = re.sub(r"[^a-z0-9]+", "", (b.get("name") or "").lower())
            if na and na == nb:
                continue
            d = dist(a, b)
            if d <= max_m:
                pairs.append({"a": a, "b": b, "meters": int(d)})
    pairs.sort(key=lambda x: x["meters"])
    return pairs[:8]
