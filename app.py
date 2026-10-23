from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import httpx

from analyze.vision import load_gemini_key

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from analyze.pipeline import analyze_stream
from analyze.learning import learning_status, record_feedback

ROOT = Path(__file__).resolve().parent
FRONT = ROOT / "frontend"
UPLOADS = Path(tempfile.gettempdir()) / "wherewas-uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif"}

app = FastAPI(title="WhereWas")


class Feedback(BaseModel):
    analysis_id: str
    selected_id: str
    candidates: list[dict]


@app.on_event("startup")
def _warmup():
    import threading
    from analyze.ocr_tools import _engine

    key = load_gemini_key()
    if key:
        os.environ["GEMINI_API_KEY"] = key
    if os.getenv("WHEREWAS_LIGHTWEIGHT") != "1":
        threading.Thread(target=_engine, daemon=True).start()


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "wherewas",
        "engine": "multi-clue-consensus-v2",
        "gemini": bool(load_gemini_key()),
        "learning": learning_status(),
    }


@app.get("/api/geocode")
async def geocode(q: str):
    query = q.strip()
    if len(query) < 2:
        return {"results": []}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0),
            headers={"User-Agent": "WhereWas/1.0 local visual search"},
        ) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "jsonv2", "limit": 6, "addressdetails": 1},
            )
            response.raise_for_status()
        results = []
        for item in response.json():
            try:
                results.append({
                    "name": item.get("name") or item.get("display_name", "").split(",")[0],
                    "address": item.get("display_name", ""),
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                    "type": item.get("type") or item.get("category") or "place",
                })
            except (KeyError, TypeError, ValueError):
                continue
        return {"results": results}
    except (httpx.HTTPError, ValueError):
        return {"results": []}


@app.post("/api/feedback")
def feedback(body: Feedback):
    try:
        status = record_feedback(body.analysis_id, body.candidates, body.selected_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "learning": status}


@app.get("/")
def index():
    return FileResponse(FRONT / "marketing.html", headers={"Cache-Control": "no-store"})


@app.get("/try")
def try_wherewas():
    return FileResponse(FRONT / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/pricing")
def pricing():
    return FileResponse(FRONT / "pricing.html", headers={"Cache-Control": "no-store"})





@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    name = file.filename or "upload.jpg"
    suffix = Path(name).suffix.lower() or ".jpg"
    if suffix not in ALLOWED:
        raise HTTPException(400, f"Unsupported type {suffix}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "File too large (15 MB max)")
    dest = UPLOADS / f"{uuid.uuid4().hex}{suffix}"
    dest.write_bytes(data)

    async def gen():
        try:
            async for chunk in analyze_stream(dest, name, dest.stem):
                yield chunk
        finally:
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass

    return StreamingResponse(gen(), media_type="application/x-ndjson")


app.mount("/", StaticFiles(directory=FRONT, html=True), name="front")
