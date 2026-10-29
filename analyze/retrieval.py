"""Multi-region retrieval over licensed street imagery.

Google Street View is deliberately excluded from indexing. Reference imagery
must be Mapillary, KartaView, Panoramax, or another licensed source.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "google/siglip-base-patch16-224"
GLOBAL_SLUG = "_global"


def retrieval_root() -> Path:
    return Path(os.getenv("WHEREWAS_RETRIEVAL_ROOT", ROOT / "data" / "retrieval"))


def safe_city(city: str | None) -> str | None:
    if not city:
        return None
    slug = "".join(c.lower() if c.isalnum() else "-" for c in city).strip("-")
    return re.sub(r"-+", "-", slug) or None


def available_regions() -> list[str]:
    root = retrieval_root()
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and p.name != GLOBAL_SLUG
        and ((p / "index-metadata.jsonl").is_file() or (p / "metadata.jsonl").is_file())
    )


def route_regions(mind: dict[str, Any] | None, *, limit: int = 5) -> list[str]:
    """Map city/country/region clues to installed reference shards."""
    installed = available_regions()
    mind = mind or {}
    clues: list[str] = []
    for value in (mind.get("city"), *(mind.get("regional_queries") or []), *(mind.get("region_hints") or [])):
        if value:
            clues.append(str(value))
    for row in mind.get("regional_hypotheses") or []:
        clues.extend(str(row.get(k) or "") for k in ("region", "country"))
    tokens = {t for clue in clues for t in re.findall(r"[a-z0-9]+", clue.lower()) if len(t) > 2}
    aliases = {
        "bangalore": "bengaluru", "bengaluru": "bengaluru", "karnataka": "bengaluru",
        "chikkamagaluru": "chikmagalur", "chikmagaluru": "chikmagalur",
        "bombay": "mumbai", "uae": "dubai", "emirates": "dubai",
    }
    wanted = {aliases.get(token, token) for token in tokens}
    scored = []
    for region in installed:
        score = 3 if region in wanted else len(set(region.split("-")) & wanted)
        if score:
            scored.append((score, region))
    return [region for _, region in sorted(scored, key=lambda row: (-row[0], row[1]))[:limit]]


@lru_cache(maxsize=1)
def _embedder() -> tuple[Any, Any, str]:
    from transformers import AutoModel, AutoProcessor
    import torch
    model_name = os.getenv("WHEREWAS_EMBED_MODEL", DEFAULT_MODEL)
    device = "cuda" if torch.cuda.is_available() and os.getenv("WHEREWAS_USE_GPU", "1") == "1" else "cpu"
    model = AutoModel.from_pretrained(model_name).eval().to(device)
    return AutoProcessor.from_pretrained(model_name), model, device


def embed_images(paths: list[Path], batch_size: int = 16) -> np.ndarray:
    """Normalized SigLIP vectors; optional on high-memory deployments."""
    processor, model, device = _embedder()
    import torch
    vectors = []
    for start in range(0, len(paths), batch_size):
        images = []
        for path in paths[start:start + batch_size]:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
        inputs = processor(images=images, return_tensors="pt").to(device)
        with torch.inference_mode():
            output = model.get_image_features(**inputs)
            output = output / output.norm(dim=-1, keepdim=True).clamp_min(1e-12)
        vectors.append(output.detach().cpu().numpy().astype("float32"))
    return np.concatenate(vectors) if vectors else np.empty((0, 0), dtype="float32")


def _light_descriptor(path: Path) -> np.ndarray:
    """Fast scene descriptor for low-memory production services."""
    import cv2
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unreadable image: {path}")
    image = cv2.resize(image, (320, 240), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blocks: list[np.ndarray] = []
    for y0, y1 in ((0, 120), (120, 240)):
        for x0, x1 in ((0, 160), (160, 320)):
            blocks.append(cv2.calcHist([hsv[y0:y1, x0:x1]], [0, 1], None, [12, 6], [0, 180, 0, 256]).ravel())
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude, angle = cv2.cartToPolar(gx, gy, angleInDegrees=True)
    edge_hist, _ = np.histogram(angle, bins=18, range=(0, 360), weights=magnitude)
    luminance = cv2.resize(gray, (16, 12), interpolation=cv2.INTER_AREA).astype("float32").ravel() / 255.0
    vector = np.concatenate([*blocks, edge_hist.astype("float32"), luminance]).astype("float32")
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def embed_images_light(paths: Iterable[Path]) -> np.ndarray:
    vectors = [_light_descriptor(path) for path in paths]
    return np.stack(vectors).astype("float32") if vectors else np.empty((0, 0), dtype="float32")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("lat") is not None and row.get("lon") is not None:
            rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def build_index(city: str, *, batch_size: int = 16, siglip: bool = True) -> dict[str, Any]:
    """Build a region shard. Lightweight vectors are always produced."""
    slug = safe_city(city)
    if not slug or slug == GLOBAL_SLUG:
        raise ValueError("A region/city name is required")
    folder = retrieval_root() / slug
    rows = _read_jsonl(folder / "metadata.jsonl")
    usable = [(dict(row), folder / str(row.get("local_path") or "")) for row in rows]
    usable = [(row, path) for row, path in usable if path.is_file()]
    if not usable:
        raise FileNotFoundError(f"No downloaded licensed reference images in {folder}")
    for row, _ in usable:
        row["region_shard"] = slug
    paths = [path for _, path in usable]
    light = embed_images_light(paths)
    np.save(folder / "light-embeddings.npy", light)
    result: dict[str, Any] = {"city": slug, "images": len(usable), "light_dimensions": int(light.shape[1])}
    if siglip:
        try:
            heavy = embed_images(paths, batch_size)
            np.save(folder / "embeddings.npy", heavy)
            result["siglip_dimensions"] = int(heavy.shape[1])
        except (ImportError, ModuleNotFoundError) as exc:
            result["siglip_skipped"] = type(exc).__name__
    _write_jsonl(folder / "index-metadata.jsonl", (row for row, _ in usable))
    _load_shard.cache_clear()
    return result


def build_global_index() -> dict[str, Any]:
    """Merge region vectors/metadata without copying reference images."""
    root = retrieval_root()
    out = root / GLOBAL_SLUG
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    light_parts: list[np.ndarray] = []
    heavy_parts: list[np.ndarray] = []
    heavy_complete = True
    for region in available_regions():
        folder = root / region
        meta = _read_jsonl(folder / "index-metadata.jsonl")
        light_path = folder / "light-embeddings.npy"
        if not meta or not light_path.is_file():
            continue
        light = np.load(light_path).astype("float32")
        count = min(len(meta), len(light))
        rows.extend({**row, "region_shard": region} for row in meta[:count])
        light_parts.append(light[:count])
        heavy_path = folder / "embeddings.npy"
        if heavy_path.is_file():
            heavy_parts.append(np.load(heavy_path).astype("float32")[:count])
        else:
            heavy_complete = False
    if not rows:
        raise FileNotFoundError("Build at least one region index first")
    np.save(out / "light-embeddings.npy", np.concatenate(light_parts))
    if heavy_complete and heavy_parts:
        np.save(out / "embeddings.npy", np.concatenate(heavy_parts))
    else:
        (out / "embeddings.npy").unlink(missing_ok=True)
    _write_jsonl(out / "index-metadata.jsonl", rows)
    _load_shard.cache_clear()
    return {"regions": len(light_parts), "images": len(rows), "path": str(out)}


@lru_cache(maxsize=10)
def _load_shard(slug: str) -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray | None] | None:
    folder = retrieval_root() / slug
    rows = _read_jsonl(folder / "index-metadata.jsonl")
    light_path = folder / "light-embeddings.npy"
    if not rows or not light_path.is_file():
        return None
    light = np.load(light_path, mmap_mode="r")
    count = min(len(rows), len(light))
    heavy_path = folder / "embeddings.npy"
    heavy = np.load(heavy_path, mmap_mode="r")[:count] if heavy_path.is_file() else None
    return rows[:count], light[:count], heavy


def _verify_features(query_path: Path, candidate_path: Path) -> tuple[int, float, float, int, str]:
    """SIFT + homography check, with ORB fallback."""
    import cv2
    query = cv2.imread(str(query_path), cv2.IMREAD_GRAYSCALE)
    candidate = cv2.imread(str(candidate_path), cv2.IMREAD_GRAYSCALE)
    if query is None or candidate is None:
        return 0, 0.0, 0.0, 0, "unavailable"
    if hasattr(cv2, "SIFT_create"):
        detector, norm, ratio, method = cv2.SIFT_create(nfeatures=2400), cv2.NORM_L2, 0.74, "sift"
    else:
        detector, norm, ratio, method = cv2.ORB_create(nfeatures=2400), cv2.NORM_HAMMING, 0.72, "orb"
    key_a, desc_a = detector.detectAndCompute(query, None)
    key_b, desc_b = detector.detectAndCompute(candidate, None)
    if desc_a is None or desc_b is None or len(key_a) < 8 or len(key_b) < 8:
        return 0, 0.0, 0.0, 0, method
    pairs = cv2.BFMatcher(norm).knnMatch(desc_a, desc_b, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance]
    if len(good) < 8:
        return len(good), 0.0, 0.0, 0, method
    points_a = np.float32([key_a[m.queryIdx].pt for m in good])
    points_b = np.float32([key_b[m.trainIdx].pt for m in good])
    _, mask = cv2.findHomography(points_a, points_b, cv2.RANSAC, 4.0)
    inliers = int(mask.sum()) if mask is not None else 0
    if mask is None or inliers < 4:
        return inliers, 0.0, 0.0, 0, method
    selected_a = points_a[mask.ravel() > 0]
    selected_b = points_b[mask.ravel() > 0]

    def spatial_support(points: np.ndarray, image: np.ndarray) -> tuple[float, int]:
        hull = cv2.convexHull(points)
        coverage = float(cv2.contourArea(hull)) / max(1.0, float(image.shape[0] * image.shape[1]))
        cells = {
            (min(3, int(x / image.shape[1] * 4)), min(2, int(y / image.shape[0] * 3)))
            for x, y in points
        }
        return coverage, len(cells)

    coverage_a, cells_a = spatial_support(selected_a, query)
    coverage_b, cells_b = spatial_support(selected_b, candidate)
    return (
        inliers,
        round(inliers / max(1, len(good)), 3),
        round(min(coverage_a, coverage_b), 4),
        min(cells_a, cells_b),
        method,
    )


def _candidate_path(row: dict[str, Any]) -> Path | None:
    shard, local = row.get("region_shard"), row.get("local_path")
    if not shard or not local:
        return None
    path = retrieval_root() / str(shard) / str(local)
    return path if path.is_file() else None


def _nearest(vector: np.ndarray, matrix: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
    scores = np.asarray(matrix @ vector[0], dtype="float32")
    count = min(count, len(scores))
    if count <= 0:
        return np.array([]), np.array([], dtype="int64")
    ids = np.argpartition(-scores, count - 1)[:count]
    ids = ids[np.argsort(-scores[ids])]
    return scores[ids], ids


def search(
    image_path: Path,
    city: str | None = None,
    *,
    mind: dict[str, Any] | None = None,
    top_k: int = 10,
    verify_k: int = 18,
) -> list[dict[str, Any]]:
    """Search the global index plus several clue-routed shards."""
    routed = route_regions(mind or {"city": city})
    requested = safe_city(city)
    if requested in available_regions() and requested not in routed:
        routed.insert(0, requested)
    slugs = ([GLOBAL_SLUG] if _load_shard(GLOBAL_SLUG) else []) + routed
    if not slugs:
        return []
    query_light = embed_images_light([image_path])
    query_heavy = None
    if os.getenv("WHEREWAS_LIGHTWEIGHT", "0") != "1" and any((_load_shard(s) or (None, None, None))[2] is not None for s in slugs):
        try:
            query_heavy = embed_images([image_path])
        except (ImportError, ModuleNotFoundError, RuntimeError):
            pass
    pooled: dict[tuple[str, str], dict[str, Any]] = {}
    prefilter = max(top_k, verify_k)
    for slug in slugs:
        loaded = _load_shard(slug)
        if not loaded:
            continue
        rows, light, heavy = loaded
        vector, matrix, descriptor = query_light, light, "scene"
        if query_heavy is not None and heavy is not None and heavy.shape[1] == query_heavy.shape[1]:
            vector, matrix, descriptor = query_heavy, heavy, "siglip"
        similarities, ids = _nearest(vector, matrix, prefilter)
        for similarity, row_index in zip(similarities, ids):
            row = dict(rows[int(row_index)])
            shard = str(row.get("region_shard") or (slug if slug != GLOBAL_SLUG else ""))
            key = (str(row.get("provider") or "reference"), str(row.get("id") or f"{shard}:{row_index}"))
            candidate = {**row, "similarity": float(similarity), "descriptor": descriptor, "region_shard": shard}
            if key not in pooled or candidate["similarity"] > pooled[key]["similarity"]:
                pooled[key] = candidate
    candidates = sorted(pooled.values(), key=lambda row: -row["similarity"])[:prefilter]
    for index, row in enumerate(candidates):
        path = _candidate_path(row)
        verified = _verify_features(image_path, path) if path is not None and index < verify_k else (0, 0.0, 0.0, 0, "not-downloaded")
        row.update(
            verified_inliers=verified[0], inlier_ratio=verified[1], match_coverage=verified[2],
            match_grid_cells=verified[3], verifier=verified[4],
            reference_id=str(row.get("id") or ""), reference_image=row.get("image_url"),
            provider=row.get("provider", "mapillary"), attribution=row.get("attribution", "Licensed reference imagery"),
        )
    candidates.sort(
        key=lambda row: (
            min(1.0, float(row["verified_inliers"]) / 35.0) * max(0.2, float(row["inlier_ratio"])),
            float(row["similarity"]),
        ), reverse=True,
    )
    return candidates[:top_k]
