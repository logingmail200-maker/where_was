from __future__ import annotations

from pathlib import Path
import io

import exifread
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass


def _to_deg(values) -> float | None:
    try:
        d, m, s = values.values if hasattr(values, "values") else values
        def n(x):
            return float(x.num) / float(x.den) if hasattr(x, "num") else float(x)

        return n(d) + n(m) / 60.0 + n(s) / 3600.0
    except Exception:
        return None


def read_exif(path: Path) -> dict:
    meta = {
        "make": None,
        "model": None,
        "datetime": None,
        "software": None,
        "gps": None,
        "gps_error_m": None,
        "focal_mm": None,
        "has_camera_tags": False,
    }
    try:
        with path.open("rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception:
        tags = {}

    def s(key):
        v = tags.get(key)
        return str(v) if v is not None else None

    meta["make"] = s("Image Make")
    meta["model"] = s("Image Model")
    meta["datetime"] = s("EXIF DateTimeOriginal") or s("Image DateTime")
    meta["software"] = s("Image Software")
    meta["has_camera_tags"] = bool(meta["make"] or meta["model"] or tags.get("EXIF DateTimeOriginal"))
    foc = tags.get("EXIF FocalLength")
    if foc is not None:
        try:
            meta["focal_mm"] = float(foc.values[0].num) / float(foc.values[0].den) if hasattr(foc.values[0], "num") else float(foc.values[0])
        except Exception:
            pass

    lat = _to_deg(tags.get("GPS GPSLatitude"))
    lon = _to_deg(tags.get("GPS GPSLongitude"))
    lat_ref = s("GPS GPSLatitudeRef") or "N"
    lon_ref = s("GPS GPSLongitudeRef") or "E"
    if lat is not None and lon is not None:
        if lat_ref.upper().startswith("S"):
            lat = -lat
        if lon_ref.upper().startswith("W"):
            lon = -lon
        if abs(lat) <= 90 and abs(lon) <= 180 and not (lat == 0 and lon == 0):
            meta["gps"] = {"lat": lat, "lon": lon}
    err = tags.get("GPS GPSHPositioningError")
    if err is not None:
        try:
            v = err.values[0]
            meta["gps_error_m"] = float(v.num) / float(v.den) if hasattr(v, "num") else float(v)
        except Exception:
            pass
    return meta


def classify_image(path: Path, exif: dict) -> dict:
    """Photo vs screenshot — heuristic, not a guarantee."""
    kind = "photo"
    reasons = []
    software = (exif.get("software") or "").lower()
    if "screenshot" in software or "screen shot" in software:
        kind = "screenshot"
        reasons.append(f"Software tag: {exif.get('software')}")
    if not exif.get("has_camera_tags"):
        reasons.append("No camera make/model/capture time in EXIF")
        if path.suffix.lower() in {".png", ".webp"}:
            kind = "screenshot"
            reasons.append("PNG/WebP without camera tags is often a screenshot")
    try:
        im = Image.open(path)
        w, h = im.size
        ratio = max(w, h) / max(1, min(w, h))
        # Common phone screenshot aspect (not camera 4:3 / 3:2)
        if not exif.get("has_camera_tags") and 1.9 <= ratio <= 2.4:
            kind = "screenshot"
            reasons.append(f"Tall frame {w}×{h} without camera EXIF")
    except Exception:
        w = h = None
    if kind == "photo" and exif.get("has_camera_tags"):
        reasons.append("Camera EXIF present")
    return {"kind": kind, "reasons": reasons, "width": w, "height": h}


def jpeg_bytes_for_search(path: Path, max_side: int = 1400) -> bytes:
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def jpeg_vision_bundle(path: Path) -> list[tuple[str, bytes]]:
    """Return the full frame and readable overlapping crops in one bundle."""
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    width, height = image.size
    boxes = [
        ("full scene", (0.0, 0.0, 1.0, 1.0)),
        ("upper signage band", (0.0, 0.06, 1.0, 0.55)),
        ("left storefronts", (0.0, 0.16, 0.58, 0.84)),
        ("right storefronts", (0.42, 0.16, 1.0, 0.84)),
        ("central road and vehicles", (0.17, 0.28, 0.83, 0.94)),
    ]
    bundle: list[tuple[str, bytes]] = []
    for label, (left, top, right, bottom) in boxes:
        crop = image.crop((int(width * left), int(height * top), int(width * right), int(height * bottom)))
        max_side = 1800 if label != "full scene" else 1600
        scale = min(1.0, max_side / max(crop.size))
        if scale < 1.0:
            crop = crop.resize(
                (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=91, optimize=True)
        bundle.append((label, buf.getvalue()))
    return bundle
