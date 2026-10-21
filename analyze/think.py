"""Human-style search: merge OCR + Gemini + EXIF, then hunt intersections — not isolated queries."""

from __future__ import annotations

import re

from .scene import brand_tokens, detect_brands, pick_city, search_brand_scene
from .places import search_places

TRANSIT = [
    (r"\bbmtc\b", "Bengaluru", "BMTC bus"),
    (r"\bnamma metro\b", "Bengaluru", "Namma Metro"),
    (r"\bksrtc\b", "Karnataka", "KSRTC"),
    (r"\bbest\b.*\bbus\b|\bbest bus\b", "Mumbai", "BEST bus"),
    (r"\bdts\b", "Kolkata", "DTS/WBTC"),
    (r"\bauto(?:rickshaw)?s?\b|\bthree[- ]wheeler\b", None, "auto-rickshaw"),
]
SCRIPTS = [
    (r"[\u0C80-\u0CFF]", "Kannada", "Karnataka"),
    (r"[\u0B80-\u0BFF]", "Tamil", "Tamil Nadu"),
    (r"[\u0D00-\u0D7F]", "Malayalam", "Kerala"),
    (r"[\u0C00-\u0C7F]", "Telugu", "Andhra/Telangana"),
    (r"[\u0900-\u097F]", "Devanagari", None),
]
INFRA_WORDS = ("flyover", "overpass", "viaduct", "metro", "bridge", "elevated", "underpass", "junction")
GENERIC_Q = {
    "jewellers", "jewellery", "jewelry", "lifestyle", "store", "shop", "mart",
    "eye", "eyeplus", "titan eye+", "kayan", "india", "photo", "image",
}


def _text(*parts) -> str:
    bits = []
    for p in parts:
        if isinstance(p, str):
            bits.append(p)
        elif isinstance(p, list):
            bits.extend(str(x) for x in p)
        elif isinstance(p, dict):
            bits.append(" ".join(str(v) for v in p.values() if isinstance(v, (str, int, float))))
    return " ".join(bits)


def compose(ocr: dict, visual: dict, exif: dict, kind: dict, rev: dict) -> dict:
    """One mind: every clue linked, not a pile of independent searches."""
    ocr_text = ocr.get("text") or ""
    gemini_blob = _text(
        visual.get("explanation"),
        visual.get("searchableNames"),
        visual.get("regionHints"),
        visual.get("languagesSeen"),
        " ".join(f"{c.get('kind')} {c.get('observation')} {c.get('geographicImplication')}" for c in visual.get("clues") or []),
    )
    blob = f"{ocr_text} {gemini_blob}".lower()
    thoughts: list[str] = []

    brands = detect_brands(visual, ocr)
    city = pick_city(visual, ocr)
    transit_hits = []
    for pat, cty, label in TRANSIT:
        if re.search(pat, blob, re.I):
            transit_hits.append(label)
            if cty and not city:
                city = cty
            elif cty and city and cty.lower() not in city.lower() and cty in ("Bengaluru", "Mumbai", "Kolkata"):
                # Transit agency beats a vague country hint
                if city.lower() in {"india", "karnataka", "asia"}:
                    thoughts.append(f"{label} overrides loose region “{city}” → {cty}.")
                    city = cty
            elif cty:
                thoughts.append(f"{label} supports {cty}.")

    scripts = []
    for pat, name, region in SCRIPTS:
        if re.search(pat, ocr_text) or (name.lower() in blob):
            scripts.append(name)
            if region and (not city or city.lower() in {"india", "asia"}):
                city = {"Karnataka": "Bengaluru"}.get(region, city)
                thoughts.append(f"{name} script on the sign → {region}.")

    infra = list(ocr.get("infra_words") or [])
    for w in INFRA_WORDS:
        if w in blob and w not in infra:
            infra.append(w)
    elevated = any(w in infra for w in ("flyover", "viaduct", "elevated", "bridge"))
    if elevated:
        thoughts.append("Concrete viaduct/flyover is a second geometric constraint — keep only outlets next to elevated structure, not underground metro.")

    # Vision-only names are suggestions, not search evidence. Requiring OCR
    # agreement prevents a hallucinated neighboring brand from creating a pin.
    agreed_visual = [a.get("vision") for a in ocr.get("vision_agreements") or [] if a.get("vision")]
    # A road name is useful only as context for an independently read sign. It
    # may broaden the search, but cannot create a precise candidate by itself.
    visual_roads = [
        n for n in (visual.get("searchableNames") or [])
        if re.search(r"\b(road|rd|street|st|main|avenue|ave|highway|hwy)\b", n or "", re.I)
    ]
    names = []
    for n in agreed_visual + (ocr.get("names") or []) + visual_roads:
        if n and n.lower() not in GENERIC_Q:
            names.append(n)
    stop = brand_tokens(brands)
    distinctive = [
        n for n in names
        if n.lower() not in stop
        and n.lower() not in GENERIC_Q
        and not any(t in n.lower() for t in stop if len(t) > 4)
    ]
    # OCR reads are independent evidence. Keep them available to the place
    # search even when a vision model cannot confidently repeat an uncommon
    # local business name. Generic terms were removed above.
    search_names = list(dict.fromkeys(
        n for n in names + (visual.get("searchableNames") or [])
        if n and len(n.strip()) >= 5 and n.lower() not in GENERIC_Q
    ))

    if brands and city:
        plan = "intersect"
        brand_s = " + ".join(b["label"] for b in brands)
        extra = " + " + "/".join(infra) if infra else ""
        thoughts.append(f"Human query: “{brand_s} in {city}{extra}”, never the bare brand word.")
    elif ocr.get("coordinates"):
        plan = "coords"
        thoughts.append("Printed coordinates / plus code / Maps URL dominate until they conflict.")
    elif brands:
        plan = "brands"
        thoughts.append("A brand is readable but the city is not; return a broad branch shortlist, not a pin.")
    elif distinctive:
        plan = "names"
        thoughts.append("No brand∩city lock — search distinctive names only.")
    else:
        plan = "weak"
        thoughts.append("Not enough interlocking clues for a small radius.")

    if len(brands) >= 2:
        thoughts.append(
            f"{brands[0]['label']} and {brands[1]['label']} in one frame → same street. Rank pairs within 800 m."
        )

    driving = visual.get("drivingSide")
    if driving and driving != "unclear":
        thoughts.append(f"Traffic appears {driving}-hand — country-scale, not a pin.")

    return {
        "city": city,
        "brands": brands,
        "infra": infra,
        "transit": transit_hits,
        "scripts": scripts,
        "names": list(dict.fromkeys(names)),
        "distinctive": list(dict.fromkeys(distinctive)),
        "plan": plan,
        "thoughts": thoughts,
        "elevated": elevated,
        "driving": driving,
        "languages": visual.get("languagesSeen") or scripts,
        "visual_names": visual.get("searchableNames") or [],
        "search_names": search_names,
        "phones": ocr.get("phones") or [],
    }


async def hunt(mind: dict, gps) -> dict:
    """Execute the interrelated plan. Brand scene wins over stray Nominatim."""
    scene = {"hits": [], "pairs": [], "groups": [], "city": mind.get("city")}
    nominatim: list = []
    # Metadata is a clue, not a reason to skip independent scene verification.
    if mind.get("plan") == "intersect" and mind.get("city") and mind.get("brands"):
        scene = await search_brand_scene(mind["brands"], mind["city"], mind.get("infra") or [])
        # A distinctive second sign may not be in the known-brand vocabulary.
        # Search it too so it can be paired with the primary brand candidate.
        if mind.get("distinctive"):
            contextual = [
                f"{brand['label']} {name}"
                for brand in mind["brands"][:2]
                for name in mind["distinctive"][:2]
            ]
            nominatim = await search_places(contextual + mind["distinctive"][:2], mind.get("city"), max_queries=4)
    elif mind.get("plan") == "names" and mind.get("distinctive"):
        nominatim = await search_places(mind["distinctive"][:3], mind.get("city"))
    elif mind.get("plan") == "brands" and mind.get("brands"):
        nominatim = await search_places([b["label"] for b in mind["brands"][:2]], None, max_queries=2)
    return {"scene": scene, "nominatim": nominatim}
