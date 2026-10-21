from __future__ import annotations

import re
import tempfile
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

INDIA_REGIONS = {
    "AN": "Andaman and Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CH": "Chandigarh", "CG": "Chhattisgarh",
    "DD": "Dadra and Nagar Haveli and Daman and Diu", "DL": "Delhi", "GA": "Goa",
    "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh", "JH": "Jharkhand",
    "JK": "Jammu and Kashmir", "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh",
    "LD": "Lakshadweep", "MH": "Maharashtra", "ML": "Meghalaya", "MN": "Manipur",
    "MP": "Madhya Pradesh", "MZ": "Mizoram", "NL": "Nagaland", "OD": "Odisha",
    "PB": "Punjab", "PY": "Puducherry", "RJ": "Rajasthan", "SK": "Sikkim",
    "TN": "Tamil Nadu", "TS": "Telangana", "TR": "Tripura", "UK": "Uttarakhand",
    "UP": "Uttar Pradesh", "WB": "West Bengal",
}

_ocr = None


def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def _ocr_pil(engine, im: Image.Image) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        p = Path(f.name)
    try:
        im.convert("RGB").save(p, format="JPEG", quality=90)
        result, _ = engine(str(p))
    except Exception:
        result = None
    finally:
        p.unlink(missing_ok=True)
    lines = []
    if result:
        for item in result:
            try:
                box = item[0] if item else None
                text = (item[1] or "").strip()
                score = float(item[2]) if len(item) > 2 else 1.0
            except Exception:
                continue
            if text:
                lines.append({"text": text, "score": round(score, 3), "box": box})
    return lines


def run_ocr(path: Path) -> dict:
    """Multi-scale OCR with targeted sign crops and a low-confidence rotation fallback."""
    engine = _engine()
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = im.size
    # A lightly enhanced, enlarged pass recovers many small storefront characters.
    enhanced = ImageEnhance.Contrast(im).enhance(1.35).filter(ImageFilter.SHARPEN)
    if max(w, h) < 2200:
        scale = min(2.0, 2200 / max(w, h))
        enhanced = enhanced.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    chunks = [("full", im), ("enhanced", enhanced)]
    m = max(80, int(min(w, h) * 0.22))
    edge_chunks = [
        ("top", im.crop((0, 0, w, m))),
        ("bottom", im.crop((0, h - m, w, h))),
        ("left", im.crop((0, 0, m, h))),
        ("right", im.crop((w - m, 0, w, h))),
    ]

    # Storefronts, road signs and vehicle markings are usually concentrated in
    # horizontal bands. A full-frame OCR pass can miss them because their text
    # occupies only a few pixels of a large photo. These crops are deliberately
    # used only when the broad pass has little dependable text.
    sign_chunks = [
        ("upper-band", im.crop((0, int(h * 0.12), w, int(h * 0.52)))),
        ("centre-band", im.crop((0, int(h * 0.30), w, int(h * 0.76)))),
        ("left-storefront", im.crop((0, int(h * 0.18), int(w * 0.56), int(h * 0.82)))),
        ("right-storefront", im.crop((int(w * 0.44), int(h * 0.18), w, int(h * 0.82)))),
    ]
    # A sign can occupy a very small part of a Street View frame.  Add
    # overlapping middle tiles so the OCR model sees lettering at a useful
    # scale instead of trying to read it beside an entire road and sky.
    # These are a fallback only; they do not change the fast path for a photo
    # that already has dependable text.
    tile_chunks = []
    for row, top in enumerate((0.12, 0.30, 0.48)):
        for col, left in enumerate((0.0, 0.24, 0.48)):
            right, bottom = min(1.0, left + 0.52), min(0.90, top + 0.36)
            tile_chunks.append((f"sign-tile-{row}-{col}", im.crop((int(w * left), int(h * top), int(w * right), int(h * bottom)))))
    seen, lines = set(), []
    for tag, crop in chunks:
        for row in _ocr_pil(engine, crop):
            key = row["text"].lower()
            if key in seen:
                continue
            seen.add(key)
            row = {**row, "pass": tag}
            lines.append(row)
    reliable = [x for x in lines if x.get("score", 0) >= 0.72 and len(x.get("text", "")) >= 3]
    # Targeted crops recover small storefront lettering before the more costly
    # edge and rotation fallbacks. Each crop is enlarged independently so text
    # is not diluted by the rest of the image.
    if len(reliable) < 2:
        for tag, crop in sign_chunks:
            scale = min(2.25, 2200 / max(crop.size))
            enlarged = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.Resampling.LANCZOS) if scale > 1 else crop
            enhanced_crop = ImageEnhance.Contrast(enlarged).enhance(1.45).filter(ImageFilter.SHARPEN)
            for row in _ocr_pil(engine, enhanced_crop):
                key = row["text"].lower()
                if key in seen:
                    continue
                seen.add(key)
                lines.append({**row, "pass": tag})
        reliable = [x for x in lines if x.get("score", 0) >= 0.72 and len(x.get("text", "")) >= 3]
    if len(reliable) < 2:
        for tag, crop in tile_chunks:
            scale = min(3.25, 2400 / max(crop.size))
            enlarged = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.Resampling.LANCZOS) if scale > 1 else crop
            # Local contrast works better for shaded storefronts than a single
            # global contrast adjustment.
            local = ImageOps.autocontrast(enlarged, cutoff=1)
            local = ImageEnhance.Contrast(local).enhance(1.5).filter(ImageFilter.UnsharpMask(radius=1.4, percent=165, threshold=2))
            for row in _ocr_pil(engine, local):
                key = row["text"].lower()
                if key in seen:
                    continue
                seen.add(key)
                lines.append({**row, "pass": tag})
        reliable = [x for x in lines if x.get("score", 0) >= 0.72 and len(x.get("text", "")) >= 3]
    # Edge crops are especially useful for screenshots and roadside framing.
    if len(reliable) < 2:
        for tag, crop in edge_chunks:
            for row in _ocr_pil(engine, crop):
                key = row["text"].lower()
                if key in seen:
                    continue
                seen.add(key)
                lines.append({**row, "pass": tag})
        reliable = [x for x in lines if x.get("score", 0) >= 0.72 and len(x.get("text", "")) >= 3]
    # Only pay for rotated passes when all normal passes found little text.
    if len(reliable) < 2:
        for angle in (90, 270):
            for row in _ocr_pil(engine, im.rotate(angle, expand=True)):
                key = row["text"].lower()
                if key in seen:
                    continue
                seen.add(key)
                lines.append({**row, "pass": f"rot{angle}"})
    raw = "\n".join(x["text"] for x in lines)
    plus = parse_plus_codes(raw)
    urls = parse_urls(raw)
    coords = parse_coordinates(raw) + parse_maps_urls(raw, urls) + decode_plus_codes(plus)
    # dedupe coords
    uniq, used = [], set()
    for c in coords:
        k = (round(c["lat"], 5), round(c["lon"], 5))
        if k in used:
            continue
        used.add(k)
        uniq.append(c)
    return {
        "text": raw,
        "lines": lines[:120],
        "plates": parse_indian_plates(raw),
        "coordinates": uniq[:8],
        "plus_codes": plus,
        "phones": parse_phone_numbers(raw),
        "names": extract_names(raw),
        "urls": urls,
        "infra_words": parse_infra(raw),
        "brands": extract_brands(raw),
    }


def parse_phone_numbers(text: str) -> list[str]:
    """High-value searchable phone numbers, normalized without separators."""
    found = []
    for match in re.finditer(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d(?:[\s-]?\d){8})(?!\d)", text or ""):
        number = re.sub(r"\D", "", match.group(1))
        if len(number) == 10 and number not in found:
            found.append(number)
    return found[:6]


def parse_infra(text: str) -> list[str]:
    hits = []
    blob = (text or "").lower()
    for w in ("flyover", "overpass", "viaduct", "metro", "subway", "bridge", "junction", "roundabout"):
        if w in blob:
            hits.append(w)
    return hits


def extract_brands(text: str) -> list[str]:
    """Repeated proper names / known retail-ish phrases — not a closed list of one city."""
    names = extract_names(text)
    return [n for n in names if len(n.split()) <= 4][:8]


def parse_indian_plates(text: str) -> list[dict]:
    found, seen = [], set()
    re_pl = re.compile(
        r"\b(AN|AP|AR|AS|BR|CH|CG|DD|DL|GA|GJ|HR|HP|JH|JK|KA|KL|LA|LD|MH|ML|MN|MP|MZ|NL|OD|PB|PY|RJ|SK|TN|TS|TR|UK|UP|WB)"
        r"[\s\-]*([0-9]{1,2})[\s\-]*([A-Z]{0,3})[\s\-]*([0-9]{4})\b",
        re.I,
    )
    for m in re_pl.finditer(text or ""):
        state = m.group(1).upper()
        plate = re.sub(r"[\s\-]+", "", m.group(0).upper())
        if plate in seen:
            continue
        seen.add(plate)
        found.append({"plate": plate, "region": INDIA_REGIONS.get(state, state), "state_code": state})
    return found


def parse_coordinates(text: str) -> list[dict]:
    found = []
    for m in re.finditer(
        r"(?<!\d)([+-]?\d{1,2}\.\d{3,8})\s*[,/ ]\s*([+-]?\d{1,3}\.\d{3,8})(?!\d)",
        text or "",
    ):
        lat, lon = float(m.group(1)), float(m.group(2))
        if abs(lat) <= 90 and abs(lon) <= 180 and not (abs(lat) < 0.01 and abs(lon) < 0.01):
            found.append({"lat": lat, "lon": lon, "raw": m.group(0), "via": "decimal"})
    return found[:5]


def parse_maps_urls(text: str, urls: list[str]) -> list[dict]:
    blob = (text or "") + "\n" + "\n".join(urls or [])
    found = []
    pats = [
        (r"/@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)", "maps-at"),
        (r"[?&](?:q|query)=(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)", "maps-q"),
        (r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)", "maps-place"),
        (r"ll=(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)", "maps-ll"),
    ]
    for pat, via in pats:
        for m in re.finditer(pat, blob, re.I):
            lat, lon = float(m.group(1)), float(m.group(2))
            if abs(lat) <= 90 and abs(lon) <= 180:
                found.append({"lat": lat, "lon": lon, "raw": m.group(0), "via": via})
    return found[:6]


def parse_plus_codes(text: str) -> list[str]:
    codes = re.findall(r"\b([23456789CFGHJMPQRVWX]{4,8}\+[23456789CFGHJMPQRVWX]{2,4})\b", text or "", re.I)
    return list(dict.fromkeys(c.upper() for c in codes))[:5]


def decode_plus_codes(codes: list[str]) -> list[dict]:
    out = []
    try:
        from openlocationcode import openlocationcode as olc
    except Exception:
        return out
    for c in codes:
        try:
            if not olc.isValid(c) or olc.isShort(c):
                continue
            a = olc.decode(c)
            out.append(
                {
                    "lat": a.latitudeCenter,
                    "lon": a.longitudeCenter,
                    "raw": c,
                    "via": "plus-code",
                    "radius_m": max(15, int(max(a.latitudeHi - a.latitudeLo, a.longitudeHi - a.longitudeLo) * 111000 / 2)),
                }
            )
        except Exception:
            continue
    return out


def parse_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s)\]>]+", text or "", re.I)
    return list(dict.fromkeys(urls))[:8]


def apply_gemini_signs(ocr: dict, visual: dict) -> dict:
    """Record visual-model readings independently; never rewrite OCR evidence."""
    import difflib

    names = [n for n in (visual.get("searchableNames") or []) if n and len(n) >= 4]
    agreements = []
    for name in names:
        tokens = [x for x in re.findall(r"[a-z0-9]+", name.lower()) if len(x) >= 4]
        compact_name = re.sub(r"[^a-z0-9]", "", name.lower())
        for line in list(ocr.get("lines") or []):
            t = (line.get("text") or "").strip()
            if not t or len(t) > 24:
                continue
            compact_ocr = re.sub(r"[^a-z0-9]", "", t.lower())
            comparisons = tokens + ([compact_name] if compact_name else [])
            ratio = max((difflib.SequenceMatcher(None, compact_ocr, x).ratio() for x in comparisons), default=0.0)
            if ratio >= 0.82:
                agreements.append({"ocr": t, "vision": name, "similarity": round(ratio, 3)})
                break
    ocr["vision_names"] = names
    ocr["vision_agreements"] = agreements
    # Only consensus readings enter OCR-derived names. Vision-only names remain
    # available separately to the reasoning stage as weaker evidence.
    agreed_names = [a["vision"] for a in agreements]
    if agreed_names:
        ocr["names"] = list(dict.fromkeys(agreed_names + (ocr.get("names") or [])))
    return ocr


def extract_names(text: str) -> list[str]:
    skip = {
        "the", "and", "for", "with", "from", "this", "that", "your", "have", "screenshot",
        "google", "maps", "search", "image", "photo", "camera", "samsung", "apple", "iphone",
        "gallery", "chrome", "android", "settings",
    }
    names = []
    for line in re.split(r"[\n|;]+", text or ""):
        line = re.sub(r"[^\w\s.&'\-]", " ", line, flags=re.UNICODE).strip()
        if len(line) < 5:
            continue
        words = line.split()
        # A storefront brand is often a single OCR token ("IciciBank"). Keep
        # it as its own clue instead of requiring it to be part of a phrase.
        if 1 <= len(words) <= 7 and sum(ch.isalpha() for ch in line) >= 5:
            names.append(line)
    # Do not let \s bridge line breaks: independent signs otherwise become one
    # fake business name such as "CareerEducation IciciBank Emission".
    for m in re.finditer(r"\b([A-Z][a-zA-Z]{2,}(?:[ \t]+[A-Z][a-zA-Z]{2,}){0,4})\b", text or ""):
        names.append(m.group(1))
    cleaned, seen = [], set()
    for n in names:
        k = re.sub(r"\s+", " ", n).strip()
        if len(k) < 5 or k.lower() in seen or k.lower() in skip:
            continue
        seen.add(k.lower())
        cleaned.append(k)
        if len(cleaned) >= 14:
            break
    return cleaned
