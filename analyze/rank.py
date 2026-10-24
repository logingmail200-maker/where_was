from __future__ import annotations

import math
from collections import Counter
from urllib.parse import quote_plus

from .learning import learned_score


def _reconcile_coords(coords: list[dict]) -> list[dict]:
    """Drop plus-codes that contradict clearer decimal / Maps-URL digits (OCR of +codes is noisy)."""
    if not coords:
        return coords
    strong = [c for c in coords if (c.get("via") or "decimal") != "plus-code"]
    plus = [c for c in coords if c.get("via") == "plus-code"]
    if strong and plus:
        plus = [p for p in plus if any(haversine_m((p["lat"], p["lon"]), (s["lat"], s["lon"])) < 2000 for s in strong)]
        return strong + plus
    return coords


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def street_view_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"


def maps_url(lat: float, lon: float, label: str | None = None) -> str:
    # Always open the exact candidate coordinates. Searching by a long place
    # label can silently select another branch of the same chain.
    query = f"{lat:.7f},{lon:.7f}"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def _city(addr: str | None) -> str:
    if not addr:
        return ""
    parts = [p.strip() for p in addr.split(",")]
    return parts[-3] if len(parts) >= 3 else (parts[-2] if len(parts) >= 2 else parts[0])


def rank(
    *,
    exif_gps: dict | None,
    gps_error_m: float | None,
    reverse_place: dict | None,
    ocr_coords: list[dict],
    plates: list[dict],
    nominatim: list[dict],
    overpass: list[dict],
    reverse_pages: list[dict],
    reverse_guesses: list[str],
    names: list[str],
    kind: str,
    pairs: list[dict] | None = None,
    elevated: list[dict] | None = None,
    infra_words: list[str] | None = None,
    regional_hypotheses: list[dict] | None = None,
) -> dict:
    candidates: list[dict] = []
    clues: list[dict] = []
    ocr_coords = _reconcile_coords(ocr_coords)

    def add_cand(**kw):
        candidates.append(kw)

    if exif_gps:
        radius = gps_error_m if gps_error_m and gps_error_m > 0 else 30
        addr = (reverse_place or {}).get("display_name") or ""
        add_cand(
            id="exif",
            name="GPS in the file",
            address=addr,
            lat=exif_gps["lat"],
            lon=exif_gps["lon"],
            radius_m=int(max(15, radius)),
            tier="pin",
            score=86,
            why=["EXIF GPS coordinates were embedded in the image. This is metadata, not independent proof of capture."],
            source="exif",
        )
        clues.append({"icon": "⌖", "title": "EXIF GPS", "body": f"{exif_gps['lat']:.5f}, {exif_gps['lon']:.5f}"})

    for i, c in enumerate(ocr_coords):
        via = c.get("via") or "decimal"
        rad = int(c.get("radius_m") or (40 if via == "plus-code" else 80))
        coord_score = 86 if via == "plus-code" else (84 if via in {"maps-at", "maps-place", "maps-q", "maps-ll"} else 72)
        coord_tier = "block" if kind == "screenshot" and via == "decimal" else "pin"
        add_cand(
            id=f"ocr-coord-{i}",
            name="Coordinates read from the image" if via != "plus-code" else f"Plus code {c.get('raw')}",
            address=c.get("raw"),
            lat=c["lat"],
            lon=c["lon"],
            radius_m=rad,
            tier=coord_tier,
            score=coord_score,
            why=[f"Read from the picture ({via}): “{c.get('raw')}”. Typical of a Maps screenshot."],
            source="ocr-coords",
        )
        clues.append({"icon": "▤", "title": f"Coordinates in the picture ({via})", "body": c.get("raw") or ""})

    for p in plates:
        clues.append({"icon": "▣", "title": f"Plate · {p['region']}", "body": f"OCR “{p['plate']}” → {p['region']} (state-level, not a street)."})

    if reverse_guesses:
        clues.append({"icon": "◎", "title": "Reverse-search guess", "body": " · ".join(reverse_guesses[:4])})
    if reverse_pages:
        body = " · ".join(f"{x.get('site')}: {(x.get('title') or '')[:60]}" for x in reverse_pages[:4])
        clues.append({"icon": "◎", "title": f"Pages that used this image ({len(reverse_pages)})", "body": body})

    # Merge OSM hits
    pooled = nominatim + overpass
    # If a name maps to many far-apart hits, it is a chain — do not pick one
    by_name: dict[str, list] = {}
    for h in pooled:
        key = (h.get("name") or "").lower().strip()
        by_name.setdefault(key, []).append(h)

    chain_names = set()
    for key, hits in by_name.items():
        if len(hits) < 3:
            continue
        # spread?
        lats = [x["lat"] for x in hits]
        lons = [x["lon"] for x in hits]
        span = haversine_m((min(lats), min(lons)), (max(lats), max(lons)))
        if span > 4000:
            chain_names.add(key)
            clues.append(
                {
                    "icon": "⌕",
                    "title": f"Chain, not a unique shop: {hits[0].get('name')}",
                    "body": f"{len(hits)} mapped places, spread ~{int(span/1000)} km. Need a second clue (street, flyover, second brand) to pin one.",
                }
            )

    for h in pooled:
        key = (h.get("name") or "").lower().strip()
        is_chain = key in chain_names and h.get("source") != "scene"
        if h.get("source") == "scene":
            score = int(h.get("score_hint") or 70)
            why = list(h.get("why") or ["Brand + city scene search on OpenStreetMap."])
            add_cand(
                id=f"scene-{h.get('osm_id')}",
                name=h.get("name") or "Outlet",
                address=h.get("address"),
                lat=h["lat"],
                lon=h["lon"],
                radius_m=int(h.get("radius_hint") or 160),
                tier=h.get("tier_hint") or "block",
                score=score,
                why=why,
                source="scene",
            )
            continue
        q = (h.get("query") or h.get("name") or "").lower()
        regional_match = next((
            row for row in (regional_hypotheses or [])
            if q == " ".join(filter(None, [str(row.get("region") or "").strip(), str(row.get("country") or "").strip()])).lower()
        ), None)
        generic_q = q in {
            "karnataka", "bengaluru", "bangalore", "india", "tamil nadu", "maharashtra",
            "delhi", "mumbai", "chennai", "kerala",
        }
        name_l = (h.get("name") or "").lower()
        name_hit = any(n.lower() in name_l or n.lower() in (h.get("address") or "").lower() for n in names[:8])
        exact = any(n.lower() == name_l or n.lower() in name_l for n in names[:8] if len(n) >= 6)
        score = 48 + min(12, int((h.get("importance") or 0) * 40))
        if regional_match:
            score = round(38 + float(regional_match.get("confidence") or 0) * 45)
        if h.get("source") == "nominatim":
            score += 6
        if h.get("source") == "google-maps":
            score += 8
            score += round((float(h.get("query_strength") or 0.45) - 0.45) * 32)
            support_count = int(h.get("support_count") or 1)
            score += min(18, max(0, support_count - 1) * 7)
        if name_hit:
            score += 10
        if exact:
            score += 18
        if generic_q and not exact:
            score -= 16
        if reverse_guesses and any(g.lower()[:12] in name_l for g in reverse_guesses if len(g) > 8):
            score += 12
        if is_chain:
            score -= 20
        # Second clue: another named thing nearby, or a flyover if the photo mentions one
        partner = None
        for pr in pairs or []:
            for node in (pr["a"], pr["b"]):
                if node.get("osm_id") == h.get("osm_id") and abs((node.get("lat") or 0) - h["lat"]) < 1e-5:
                    partner = pr
                    break
            if partner:
                break
        near_el = None
        if infra_words:
            for f in elevated or []:
                if f.get("is_elevated") and haversine_m((h["lat"], h["lon"]), (f["lat"], f["lon"])) < 380:
                    near_el = f
                    break
        if partner:
            score += max(5, round(18 * (1 - min(200, partner["meters"]) / 200)))
        nearby_queries = {
            (other.get("query") or other.get("name") or "").lower()
            for other in pooled
            if other is not h
            and (other.get("query") or other.get("name"))
            and (other.get("query") or "").lower() != (h.get("query") or "").lower()
            and haversine_m((h["lat"], h["lon"]), (other["lat"], other["lon"])) <= 200
        }
        query_strength = float(h.get("query_strength") or 0.45)
        weak_google = h.get("source") == "google-maps" and query_strength < 0.7 and not nearby_queries
        if nearby_queries:
            score += min(24, 9 * len(nearby_queries))
        if weak_google:
            # A chain name or city-wide Google search is useful for making a
            # shortlist, but cannot resolve one branch by itself.
            score -= 30
        if near_el:
            score += 10
        # Two independently searched places within walking distance are much
        # stronger than a chain-name match. Preserve that separation in score.
        score = max(20, min(96 if partner else 86, score))
        why = [f"Place search for “{h.get('query') or h.get('name')}” via OpenStreetMap."]
        matched_queries = h.get("matched_queries") or []
        if len(matched_queries) > 1:
            why.append(f"{len(matched_queries)} independent clue combinations resolved to this same place.")
        nearby_names = [name for name in (h.get("nearby_names") or []) if name and name != h.get("name")]
        if nearby_names:
            why.append("Nearby mapped names: " + ", ".join(nearby_names[:3]) + ".")
        if partner:
            other = partner["b"] if partner["a"].get("osm_id") == h.get("osm_id") else partner["a"]
            why.append(f"Second name “{other.get('name')}” is {partner['meters']} m away — two clues, not a chain guess.")
        if near_el:
            why.append(f"Mapped {near_el.get('name')} within 350 m matches infra in the photo ({', '.join(infra_words or [])}).")
        if regional_match:
            tier = "region"
            bbox_values = h.get("boundingbox") or []
            radius = 150000
            try:
                south, north, west, east = (float(value) for value in bbox_values[:4])
                radius = int(max(25000, min(900000, haversine_m(((south + north) / 2, west), ((south + north) / 2, east)) / 2)))
            except (TypeError, ValueError):
                pass
            why = [
                "Broad environmental candidate; this is not a street-level match.",
                *[str(reason) for reason in (regional_match.get("reasons") or [])[:4]],
            ]
            counter = [str(reason) for reason in (regional_match.get("counterEvidence") or [])[:3]]
            if counter:
                why.append("Uncertainty: " + "; ".join(counter))
        elif exact and (partner or nearby_queries or len(matched_queries) >= 2) and (near_el or not infra_words) and not is_chain:
            tier, radius = "pin", 120
            why.append("Unique name plus an independently mapped nearby name.")
        elif exact:
            tier, radius = "block", 150
            why.append("Name is distinctive; still a candidate centroid until Street View confirms.")
        else:
            tier, radius = "block", 220 if h.get("source") == "nominatim" else 180
            why.append("This is a candidate centroid, not a verified camera position.")
        if is_chain:
            tier, radius = "city", max(radius, 1000)
            why.append("This name has multiple distant branches; city/branch evidence is still missing.")
        if weak_google:
            tier, radius = "city", max(radius, 1200)
            why.append("This result came from a broad search and needs a road, phone number, or nearby second place to verify the branch.")
        add_cand(
            id=f"{h.get('source')}-{h.get('osm_id')}",
            name=h.get("name") or "Place",
            address=h.get("address"),
            lat=h["lat"],
            lon=h["lon"],
            radius_m=radius,
            tier=tier,
            score=int(score),
            why=why,
            source=h.get("source"),
        )

    # Strengthen metadata only when an independently generated scene candidate agrees.
    exif_candidate = next((c for c in candidates if c.get("source") == "exif"), None)
    independent = [c for c in candidates if c.get("source") not in {"exif", "ocr-coords"}]
    if exif_candidate and independent:
        nearest = min(haversine_m((exif_candidate["lat"], exif_candidate["lon"]), (c["lat"], c["lon"])) for c in independent)
        if nearest <= 1500:
            exif_candidate["score"] = min(96, exif_candidate["score"] + 8)
            exif_candidate["why"].append(f"Independent scene evidence agrees within {int(nearest)} m.")
        elif nearest >= 50000:
            exif_candidate["score"] -= 12
            exif_candidate["tier"] = "city"
            exif_candidate["why"].append("Independent scene evidence is far away; metadata may describe capture rather than depicted place.")

    # Region-only from plates when nothing else pinned
    region_only = None
    if plates and not any(c["tier"] == "pin" for c in candidates):
        region = plates[0]["region"]
        region_only = region
        clues.append({"icon": "▣", "title": "Region only", "body": f"Plate evidence points to {region}. That is not a small radius."})

    # Deduplicate near-identical pins
    for candidate in candidates:
        candidate["base_score"] = candidate["score"]
        candidate["score"] = round(learned_score(candidate), 2)
    tier_priority = {"pin": 3, "block": 2, "city": 1, "region": 0}
    candidates.sort(key=lambda c: (-c["score"], -tier_priority.get(c.get("tier"), 0), c.get("radius_m") or 999999))
    kept = []
    for c in candidates:
        if any(haversine_m((c["lat"], c["lon"]), (k["lat"], k["lon"])) < 40 and c["name"] == k["name"] for k in kept):
            continue
        exact_label = None if c.get("source") in {"exif", "ocr-coords"} else " ".join(filter(None, [c.get("name"), c.get("address")]))
        c["maps"] = maps_url(c["lat"], c["lon"], exact_label)
        c["street_view"] = street_view_url(c["lat"], c["lon"])
        kept.append(c)
        if len(kept) >= 8:
            break

    # A low-scoring branch suggestion is not a resolved location. This prevents
    # a chain name alone from becoming a confident-looking pin.
    primary = kept[0] if kept and kept[0]["score"] >= 65 and kept[0].get("tier") not in {"city", "region"} else None
    # Honest tier: many similar scores → don't pretend we know
    if primary and primary["tier"] != "pin" and len(kept) >= 2:
        if kept[0]["score"] - kept[1]["score"] < 8:
            primary = {**primary, "tier": "city" if primary["tier"] == "block" else primary["tier"]}
            primary["why"] = list(primary["why"]) + ["Top candidates are too close in score — treat as a shortlist, not a single pin."]
            kept[0] = primary

    summary = _summary(primary, kept, plates, kind, bool(reverse_pages), region_only)
    return {
        "candidates": kept,
        "primary": primary,
        "clues": clues,
        "region_only": region_only,
        "summary": summary,
    }


def _summary(primary, kept, plates, kind, had_reverse, region_only) -> str:
    if primary and primary.get("source") == "exif":
        return "The file contains GPS metadata. Shown as a pin; still confirm it matches what you see."
    if primary and primary.get("source") == "ocr-coords":
        return "Digits that look like coordinates were read from the screenshot. Confirm they are not a watermark or another place."
    if primary and len(kept) == 1 and primary["score"] >= 70:
        return "One place candidate stood out from text / reverse search / OSM. Radius is a block-scale estimate, not a survey."
    if kept:
        if any(candidate.get("tier") == "region" for candidate in kept):
            return f"{len(kept)} broad environmental candidates. Vegetation and terrain can narrow a region, but cannot honestly identify an exact road alone."
        return f"{len(kept)} place candidates. Open Street View on each — automatic worldwide street-view matching is not in this pass."
    if region_only:
        return f"Nothing unique enough to pin. Best region signal: {region_only}."
    if had_reverse:
        return "Reverse search ran but did not yield a unique mapped place. Try a tighter crop of a sign or facade."
    if kind == "screenshot":
        return "Screenshot with no GPS, no readable coordinates, and no unique name. Crop to the place (drop UI chrome) and retry."
    return "Not enough distinctive evidence for a small region. A shop name, plate, or landmark in frame is needed."
