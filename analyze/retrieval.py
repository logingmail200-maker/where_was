"""Licensed street-image retrieval used after coarse visual routing.

Indexes are deliberately city-sharded: ``data/retrieval/<city>/`` contains
Mapillary/KartaView images, metadata.jsonl, embeddings.npy and index.faiss.
Nothing in this module downloads or indexes Google Street View.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "google/siglip-base-patch16-224"


def retrieval_root() -> Path:
    return Path(os.getenv("WHEREWAS_RETRIEVAL_ROOT", ROOT / "data" / "retrieval"))


def safe_city(city: str | None) -> str | None:
    if not city:
        return None
    slug = "".join(char.lower() if char.isalnum() else "-" for char in city).strip("-")
    return slug or None


@lru_cache(maxsize=1)
def _embedder() -> tuple[Any, Any, str]:
    """Load once, on demand. The model download is a setup operation, not HTTP work."""
    from transformers import AutoModel, AutoProcessor
    import torch

    model_name = os.getenv("WHEREWAS_EMBED_MODEL", DEFAULT_MODEL)
    device = "cuda" if torch.cuda.is_available() and os.getenv("WHEREWAS_USE_GPU", "1") == "1" else "cpu"
    model = AutoModel.from_pretrained(model_name).eval().to(device)
    return AutoProcessor.from_pretrained(model_name), model, device


def embed_images(paths: list[Path], batch_size: int = 16) -> np.ndarray:
    """Create L2-normalized SigLIP image embeddings for a list of images."""
    processor, model, device = _embedder()
    import torch

    vectors: list[np.ndarray] = []
    for start in range(0, len(paths), batch_size):
        images = []
        for path in paths[start : start + batch_size]:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
        inputs = processor(images=images, return_tensors="pt").to(device)
        with torch.inference_mode():
            output = model.get_image_features(**inputs)
            output = output / output.norm(dim=-1, keepdim=True).clamp_min(1e-12)
        vectors.append(output.detach().cpu().numpy().astype("float32"))
    return np.concatenate(vectors, axis=0) if vectors else np.empty((0, 0), dtype="float32")


def _read_metadata(city_dir: Path) -> list[dict[str, Any]]:
    metadata = city_dir / "metadata.jsonl"
    if not metadata.is_file():
        return []
    rows = []
    for line in metadata.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("local_path") and row.get("lat") is not None and row.get("lon") is not None:
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def build_index(city: str, *, batch_size: int = 16) -> dict[str, Any]:
    """Embed downloaded licensed imagery and create a FAISS cosine-similarity index."""
    slug = safe_city(city)
    if not slug:
        raise ValueError("A city name is required")
    city_dir = retrieval_root() / slug
    rows = _read_metadata(city_dir)
    usable = [(row, city_dir / row["local_path"]) for row in rows if (city_dir / row["local_path"]).is_file()]
    if not usable:
        raise FileNotFoundError(f"No downloaded reference images in {city_dir}")
    vectors = embed_images([path for _, path in usable], batch_size=batch_size)
    if not len(vectors):
        raise RuntimeError("No embeddings were generated")
    import faiss

    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    faiss.write_index(index, str(city_dir / "index.faiss"))
    np.save(city_dir / "embeddings.npy", vectors)
    (city_dir / "index-metadata.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row, _ in usable), encoding="utf-8"
    )
    return {"city": slug, "images": len(usable), "dimensions": int(vectors.shape[1]), "index": str(city_dir / "index.faiss")}


@lru_cache(maxsize=4)
def _load_index(city: str) -> tuple[Any, list[dict[str, Any]]] | None:
    city_dir = retrieval_root() / city
    index_path, metadata_path = city_dir / "index.faiss", city_dir / "index-metadata.jsonl"
    if not index_path.is_file() or not metadata_path.is_file():
        return None
    import faiss

    rows = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return faiss.read_index(str(index_path)), rows


def _verify_orb(query_path: Path, candidate_path: Path) -> tuple[int, float]:
    """CPU fallback geometric check. Use it only on the nearest visual matches."""
    import cv2

    query = cv2.imread(str(query_path), cv2.IMREAD_GRAYSCALE)
    candidate = cv2.imread(str(candidate_path), cv2.IMREAD_GRAYSCALE)
    if query is None or candidate is None:
        return 0, 0.0
    orb = cv2.ORB_create(nfeatures=1600)
    key_a, desc_a = orb.detectAndCompute(query, None)
    key_b, desc_b = orb.detectAndCompute(candidate, None)
    if desc_a is None or desc_b is None or len(key_a) < 8 or len(key_b) < 8:
        return 0, 0.0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
    good = [first for first, second in matches if first.distance < 0.72 * second.distance]
    if len(good) < 8:
        return len(good), 0.0
    points_a = np.float32([key_a[match.queryIdx].pt for match in good])
    points_b = np.float32([key_b[match.trainIdx].pt for match in good])
    _, mask = cv2.findHomography(points_a, points_b, cv2.RANSAC, 5.0)
    inliers = int(mask.sum()) if mask is not None else 0
    return inliers, round(inliers / max(1, len(good)), 3)


def search(image_path: Path, city: str | None, *, top_k: int = 10, verify_k: int = 24) -> list[dict[str, Any]]:
    """Return verified, licensed-reference candidates for one routed city.

    Absence of an index is normal and produces no results; it never silently
    falls back to web-wide imagery or Google Street View.
    """
    slug = safe_city(city)
    if not slug:
        return []
    loaded = _load_index(slug)
    if not loaded:
        return []
    index, rows = loaded
    vector = embed_images([image_path])
    if not len(vector):
        return []
    count = min(max(top_k, verify_k), len(rows))
    similarities, ids = index.search(vector, count)
    candidates = []
    city_dir = retrieval_root() / slug
    for similarity, row_index in zip(similarities[0], ids[0]):
        if row_index < 0:
            continue
        row = rows[int(row_index)]
        local_path = city_dir / row["local_path"]
        inliers, inlier_ratio = _verify_orb(image_path, local_path)
        candidates.append(
            {
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "similarity": float(similarity),
                "verified_inliers": inliers,
                "inlier_ratio": inlier_ratio,
                "reference_id": str(row.get("id") or ""),
                "reference_image": row.get("image_url"),
                "provider": row.get("provider", "mapillary"),
                "attribution": row.get("attribution", "© Mapillary, CC-BY-SA"),
                "captured_at": row.get("captured_at"),
            }
        )
    candidates.sort(key=lambda row: (row["verified_inliers"], row["inlier_ratio"], row["similarity"]), reverse=True)
    return candidates[:top_k]
