from __future__ import annotations

import asyncio
import math
import re

from .places import OVERPASS, _client, bbox_around, nearby_elevated, reverse_geocode, search_near, search_places

# Shop name vs Indian toponym — "Kalyan" is both a jewellery chain and a city.
BRAND_PATTERNS = [
    {"id": "icici-bank", "match": r"\bicici\s*bank\b|\bicicibank\b", "osm": r"ICICI", "label": "ICICI Bank"},
    {"id": "emission-testing", "match": r"\bemissio(?:n)?\b|\bpollution\s+(?:test|check)", "osm": r"Emission|Pollution", "label": "Emission Testing Center"},
    {"id": "kalyan", "match": r"\bkalyan\b", "osm": r"Kalyan", "label": "Kalyan Jewellers"},
    {"id": "candere", "match": r"\bcandere\b", "osm": r"Candere", "label": "Candere"},
    {"id": "titan", "match": r"\btitan\b", "osm": r"Titan", "label": "Titan"},
    {"id": "reliance", "match": r"\breliance\b", "osm": r"Reliance", "label": "Reliance"},
    {"id": "more", "match": r"\bmore supermarket\b|\bmore megastore\b", "osm": r"More", "label": "More"},
    {"id": "phoenix", "match": r"\bphoenix\b", "osm": r"Phoenix", "label": "Phoenix"},
]

CITY_FROM_CLUE = [
    (r"\bbmtc\b|\bnamma metro\b|\bbengaluru\b|\bbangalore\b", "Bengaluru"),
    (r"\bbest bus\b|\blocal train\b", "Mumbai"),
    (r"\bmtc\b|\bchennai\b", "Chennai"),
    (r"\bdts\b|\bkolkata\b", "Kolkata"),
]

COUNTRY_OR_STATE = {
    "india", "asia", "karnataka", "maharashtra", "tamil nadu", "kerala",
    "andhra pradesh", "telangana", "delhi", "gujarat", "rajasthan",
}


def _blob(*parts) -> str:
    bits = []
    for p in parts:
        if isinstance(p, str):
            bits.append(p)
        elif isinstance(p, list):
            bits.extend(str(x) for x in p)
        elif isinstance(p, dict):
            bits.append(" ".join(str(v) for v in p.values() if isinstance(v, str)))
    return " ".join(bits).lower()


def pick_city(visual: dict, ocr: dict) -> str | None:
    blob = _blob(
        ocr.get("text"),
        visual.get("explanation"),
        visual.get("regionHints"),
        visual.get("searchableNames"),
        " ".join(f"{c.get('kind')} {c.get('observation')} {c.get('geographicImplication')}" for c in visual.get("clues") or []),
    )
    for pat, city in CITY_FROM_CLUE:
        if re.search(pat, blob, re.I):
            return city
    hints = visual.get("regionHints") or []
    for h in reversed(hints):
        if (h or "").strip().lower() not in COUNTRY_OR_STATE:
            return h
    for h in reversed(hints):
        if (h or "").strip().lower() not in {"india", "asia"}:
            return h
    return None


def detect_brands(visual: dict, ocr: dict) -> list[dict]:
    agreed = [a.get("vision") or "" for a in ocr.get("vision_agreements") or []]
    blob = _blob(
        ocr.get("text"),
        agreed,
    )
    found = []
    for b in BRAND_PATTERNS:
        # Accept exact or mildly damaged OCR (for example KAYAN → KALYAN),
        # but never a vision-only brand reading.
        target = b["id"]
        tokens = re.findall(r"[a-z0-9]+", blob)
        fuzzy = any(__import__("difflib").SequenceMatcher(None, t, target).ratio() >= 0.82 for t in tokens)
        if re.search(b["match"], blob, re.I) or fuzzy:
            found.append(b)
    # Generalize beyond the small known-brand list, but only when OCR and the
    # visual model independently agree on the same proper name.
    known = {b["label"].lower() for b in found}
    known_ids = {b["id"] for b in found}
    for agreement in ocr.get("vision_agreements") or []:
        name = (agreement.get("vision") or "").strip()
        if not (4 <= len(name) <= 40) or name.lower() in known:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if slug and slug not in known_ids:
            found.append({"id": slug, "match": re.escape(name), "osm": re.escape(name), "label": name})
            known.add(name.lower())
            known_ids.add(slug)
    return found


def brand_tokens(brands: list[dict]) -> set[str]:
    toks = set()
    for b in brands:
        toks.add(b["id"])
        toks.add(b["label"].lower())
        toks.add(b["osm"].lower())
    toks.update({"kalyan jewellers", "titan eye", "titan eyeplus", "titan eye+"})
    return toks


def _dist_m(a, b) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(x), math.sqrt(1 - x))


async def city_bbox(city: str) -> tuple[float, float, float, float] | None:
    hits = await search_places([city], None)
    if not hits:
        if city.lower() in {"bengaluru", "bangalore"}:
            return (12.65, 77.30, 13.30, 77.95)
        return None
    h = hits[0]
    return bbox_around(h["lat"], h["lon"], 28)


async def _overpass_brand(pattern: str, bbox: tuple[float, float, float, float]) -> list[dict]:
    s, w, n, e = bbox
    area = f"({s},{w},{n},{e})"
    q = f"""[out:json][timeout:25];
    (
      nwr["name"~"{pattern}",i]{area};
      nwr["brand"~"{pattern}",i]{area};
      nwr["operator"~"{pattern}",i]{area};
    );
    out center tags 60;"""
    data = None
    async with _client() as client:
        for url in OVERPASS:
            try:
                r = await client.post(url, data={"data": q})
                if r.status_code == 200:
                    data = r.json()
                    break
            except Exception:
                continue
    if not data:
        return []
    hits = []
    for el in data.get("elements") or []:
        tags = el.get("tags") or {}
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None:
            continue
        label = tags.get("name") or tags.get("brand") or tags.get("operator")
        if not label:
            continue
        hits.append(
            {
                "source": "scene",
                "query": pattern,
                "name": label,
                "address": ", ".join(
                    filter(
                        None,
                        [tags.get("addr:street"), tags.get("addr:suburb"), tags.get("addr:city"), tags.get("addr:state")],
                    )
                )
                or None,
                "lat": float(lat),
                "lon": float(lon),
                "osm_type": el.get("type"),
                "osm_id": el.get("id"),
                "category": tags.get("shop") or tags.get("amenity") or tags.get("tourism") or "",
                "tags": tags,
            }
        )
    # unique by osm id
    seen, out = set(), []
    for h in hits:
        k = (h["osm_type"], h["osm_id"])
        if k in seen:
            continue
        seen.add(k)
        out.append(h)
    return out


async def search_brand_scene(brands: list[dict], city: str, infra_words: list[str]) -> dict:
    """Human pattern: in this city, find brand A near brand B near a flyover/metro."""
    bbox = await city_bbox(city)
    if not bbox or not brands:
        return {"hits": [], "pairs": [], "city": city, "bbox": bbox, "elevated": []}

    mapped_groups = await asyncio.gather(*(_overpass_brand(b["osm"], bbox) for b in brands))
    groups = []
    for b, mapped in zip(brands, mapped_groups):
        items = mapped
        if not items:
            # Public Overpass is often busy — Nominatim with "brand + city", never the bare toponym.
            q = [f"{b['label']} {city}"]
            nom = await search_places(q, city)
            items = [{**h, "source": "scene", "query": b["osm"]} for h in nom]
        groups.append({"brand": b, "items": items})

    primary = groups[0]["items"] if groups else []
    others = [g for g in groups[1:]]

    pairs = []
    for g in others:
        for a in primary:
            for b in g["items"]:
                d = _dist_m((a["lat"], a["lon"]), (b["lat"], b["lon"]))
                if d <= 200:
                    pairs.append({"a": a, "b": b, "meters": int(d), "brands": (groups[0]["brand"]["id"], g["brand"]["id"])})
    pairs.sort(key=lambda x: x["meters"])

    seeds = primary[:25]
    if pairs:
        seeds = [p["a"] for p in pairs[:12]] + [p["b"] for p in pairs[:12]]
    elevated = []
    if seeds:
        try:
            elevated = await nearby_elevated(seeds, radius_m=400)
        except Exception:
            elevated = []
    if not elevated:
        # Overpass down: ask Nominatim for metro/flyover next to each outlet
        for a in primary[:5]:
            await asyncio.sleep(1.05)
            found = await search_near("metro", a["lat"], a["lon"], 0.45)
            if not found:
                found = await search_near("flyover", a["lat"], a["lon"], 0.45)
            for f in found:
                elevated.append({"lat": f["lat"], "lon": f["lon"], "name": f.get("name") or "metro", "tags": {}})

    ranked = []
    for a in primary:
        loc = (a["lat"], a["lon"])
        near_b = None
        for p in pairs:
            if p["a"]["osm_id"] == a["osm_id"]:
                near_b = p
                break
        near_el = None
        for f in elevated:
            if _dist_m(loc, (f["lat"], f["lon"])) < 400:
                near_el = f
                break
        strong_el = bool(near_el and near_el.get("is_elevated"))
        score = 40
        why = [f"Mapped {a['name']} in {city} (OpenStreetMap)."]
        if near_b:
            score += round(40 * (1 - near_b["meters"] / 200))
            why.append(f"{near_b['b']['name']} is {near_b['meters']} m away.")
        if near_el and infra_words:
            nm = (near_el.get("name") or "").lower()
            # Underground metro stations are not the viaduct in the photo.
            if any(w in nm for w in ("flyover", "viaduct", "bridge", "elevated")):
                score += 28
                why.append(f"Mapped {near_el['name']} within 400 m (elevated road/rail).")
            elif "metro" in nm or "subway" in nm:
                score += 6
                why.append(f"Metro named nearby ({near_el['name']}) — may be underground, weaker than a viaduct.")
            else:
                score += 12
                why.append(f"Mapped {near_el['name']} within 400 m — matches {', '.join(infra_words)} in the photo.")
        elif near_el:
            score += 8
            why.append(f"Structure {near_el['name']} nearby.")
        if near_b and strong_el and infra_words:
            tier, radius = "pin", 110
        elif near_b or near_el:
            tier, radius = "block", 160
        else:
            tier, radius = "block", 250
        ranked.append(
            {
                **a,
                "score_hint": int(min(88, score)),
                "tier_hint": tier,
                "radius_hint": radius,
                "why": why,
                "partner": near_b,
                "elevated": near_el,
            }
        )
    ranked.sort(key=lambda x: -x["score_hint"])
    return {
        "hits": ranked[:12],
        "pairs": pairs[:8],
        "city": city,
        "bbox": bbox,
        "elevated": elevated[:20],
        "groups": [{"brand": g["brand"]["label"], "n": len(g["items"])} for g in groups],
    }
