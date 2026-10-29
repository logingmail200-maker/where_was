"""Download licensed Mapillary thumbnails and metadata for one bounded city area.

Example:
python tools/download_mapillary_reference.py --city bengaluru --bbox 12.90,77.54,12.99,77.65 --max-images 5000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import math
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze.retrieval import retrieval_root, safe_city

API = "https://graph.mapillary.com/images"
ATTRIBUTION = "© Mapillary, CC-BY-SA"
UA = "WhereWas/1.0 licensed-reference-collector"


def token() -> str:
    value = (os.getenv("MAPILLARY_ACCESS_TOKEN") or "").strip()
    if value:
        return value
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MAPILLARY_ACCESS_TOKEN="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError("MAPILLARY_ACCESS_TOKEN is not set")


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    values = tuple(float(value.strip()) for value in raw.split(","))
    if len(values) != 4:
        raise ValueError("bbox must be south,west,north,east")
    south, west, north, east = values
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("bbox coordinates are invalid")
    return values


def tile_bbox(bbox: tuple[float, float, float, float], max_span: float = 0.055) -> list[tuple[float, float, float, float]]:
    """Split large city extents; Mapillary often rejects very broad queries."""
    south, west, north, east = bbox
    rows = max(1, math.ceil((north - south) / max_span))
    cols = max(1, math.ceil((east - west) / max_span))
    lat_step, lon_step = (north - south) / rows, (east - west) / cols
    return [
        (south + row * lat_step, west + col * lon_step,
         south + (row + 1) * lat_step, west + (col + 1) * lon_step)
        for row in range(rows) for col in range(cols)
    ]


async def run(city: str, bbox: tuple[float, float, float, float], max_images: int, delay_s: float) -> None:
    slug = safe_city(city)
    if not slug:
        raise ValueError("city is required")
    city_dir = retrieval_root() / slug
    images_dir = city_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = city_dir / "metadata.jsonl"
    seen = set()
    if metadata_path.is_file():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(str(json.loads(line).get("id")))
            except json.JSONDecodeError:
                pass
    access_token = token()
    downloaded = 0
    next_url: str | None = API
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), headers={"User-Agent": UA}) as client:
        with metadata_path.open("a", encoding="utf-8") as metadata:
          for tile in tile_bbox(bbox):
            if downloaded >= max_images:
                break
            params = {
                "access_token": access_token,
                "bbox": f"{tile[1]},{tile[0]},{tile[3]},{tile[2]}",
                "limit": 200,
                "fields": "id,thumb_1024_url,computed_geometry,captured_at,compass_angle,sequence,creator",
            }
            next_url = API
            while next_url and downloaded < max_images:
                response = None
                for attempt in range(3):
                    try:
                        response = await client.get(next_url, params=params if next_url == API else None)
                        response.raise_for_status()
                        break
                    except httpx.HTTPError:
                        if attempt < 2:
                            await asyncio.sleep(1.0 * (attempt + 1))
                if response is None or response.is_error:
                    break
                payload = response.json()
                for row in payload.get("data") or []:
                    image_id = str(row.get("id") or "")
                    geometry = row.get("computed_geometry") or {}
                    coords = geometry.get("coordinates") or []
                    thumb = row.get("thumb_1024_url")
                    if image_id in seen or not thumb or len(coords) < 2:
                        continue
                    local_path = images_dir / f"{image_id}.jpg"
                    try:
                        image_response = await client.get(thumb)
                        image_response.raise_for_status()
                        local_path.write_bytes(image_response.content)
                    except httpx.HTTPError:
                        continue
                    record = {
                        "id": image_id,
                        "provider": "mapillary",
                        "license": "CC-BY-SA",
                        "attribution": ATTRIBUTION,
                        "image_url": thumb,
                        "local_path": str(local_path.relative_to(city_dir)).replace("\\", "/"),
                        "lat": float(coords[1]),
                        "lon": float(coords[0]),
                        "captured_at": row.get("captured_at"),
                        "compass_angle": row.get("compass_angle"),
                        "sequence": row.get("sequence"),
                        "creator": row.get("creator"),
                    }
                    metadata.write(json.dumps(record, ensure_ascii=False) + "\n")
                    metadata.flush()
                    seen.add(image_id)
                    downloaded += 1
                    if downloaded >= max_images:
                        break
                    await asyncio.sleep(delay_s)
                next_url = (payload.get("paging") or {}).get("next")
                if next_url and downloaded < max_images:
                    await asyncio.sleep(delay_s)
    print(f"Downloaded {downloaded} licensed Mapillary images into {city_dir}")
    print(f"Attribution required wherever reference imagery is shown: {ATTRIBUTION}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect licensed Mapillary street imagery for a bounding box.")
    parser.add_argument("--city", required=True)
    parser.add_argument("--bbox", required=True, help="south,west,north,east")
    parser.add_argument("--max-images", type=int, default=1000)
    parser.add_argument("--delay", type=float, default=0.55, help="Seconds between thumbnail/API requests")
    args = parser.parse_args()
    if not 1 <= args.max_images <= 100_000:
        raise ValueError("max-images must be between 1 and 100000")
    asyncio.run(run(args.city, parse_bbox(args.bbox), args.max_images, max(0.25, args.delay)))


if __name__ == "__main__":
    main()
