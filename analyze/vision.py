from __future__ import annotations

import json
import os
import re
import asyncio
from pathlib import Path

import httpx

MODELS = tuple(
    x.strip()
    for x in os.environ.get("GEMINI_MODELS", "gemini-3.1-flash-lite,gemini-2.5-flash,gemini-3.6-flash").split(",")
    if x.strip()
)

PROMPT = """You are a cautious visual geolocation analyst. Inspect this image for clues that narrow WHERE it was taken. Do not invent unread text. Never output coordinates, plus codes, or a street address you cannot read.

OCR already detected (may contain errors): {ocr}

Return ONLY JSON with:
- imageType: photo | screenshot | document | other
- depictedPlaceVsCapture: same | unclear | different
- regionHints: array of strings (country / state / city only if evidence supports it)
- languagesSeen: scripts or languages on signs
- drivingSide: left | right | unclear
- clues: array of {{kind, observation, geographicImplication, strength}}
  kind examples: architecture, vegetation, road furniture, terrain, signage, vehicle, weather, infrastructure, brand, text
  strength: strong | medium | weak
- searchableNames: exact visible proper names / brands / place names only (empty if unsure)
- explanation: one short sentence

Mark generic clues weak. Treat chain brands as search targets, not neighbourhood names. If a flyover/metro pillar id is legible, quote the exact characters in a clue kind "pillar identifier".
Read separate storefront signs as separate searchableNames; never merge neighboring signs into one name. Include road-label text, banks, testing centres, schools and phone-number-bearing businesses when legible.
For a local restaurant, hotel, shop or landmark, inspect the fascia, awning and roadside board separately before deciding the name is unreadable. If you can make out only part of a name, return that fragment as a clue but do not put it in searchableNames.
"""


def load_gemini_key() -> str:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def _strip_coords(obj: dict) -> dict:
    """Gemini must not become a fake GPS. Drop anything that looks like coordinates."""
    blob = json.dumps(obj, ensure_ascii=False)
    if re.search(r"\b-?\d{1,2}\.\d{3,},\s*-?\d{1,3}\.\d{3,}\b", blob):
        obj = dict(obj)
        obj["explanation"] = (obj.get("explanation") or "") + " (coordinate-like text from the model was ignored.)"
        for c in obj.get("clues") or []:
            for k in ("observation", "geographicImplication"):
                if k in c:
                    c[k] = re.sub(r"-?\d{1,2}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}", "[coords omitted]", c[k])
    obj["searchableNames"] = [n for n in (obj.get("searchableNames") or []) if not re.search(r"\d{1,2}\.\d{3,}", n)]
    return obj


async def ask_gemini(jpeg: bytes, ocr_text: str) -> dict:
    key = load_gemini_key()
    if not key:
        return {"ok": False, "error": "GEMINI_API_KEY not set", "skipped": True}

    import base64

    b64 = base64.b64encode(jpeg).decode("ascii")
    prompt = PROMPT.format(ocr=json.dumps((ocr_text or "")[:4000]))
    schema = {
        "type": "OBJECT",
        "properties": {
            "imageType": {"type": "STRING"},
            "depictedPlaceVsCapture": {"type": "STRING"},
            "regionHints": {"type": "ARRAY", "items": {"type": "STRING"}},
            "languagesSeen": {"type": "ARRAY", "items": {"type": "STRING"}},
            "drivingSide": {"type": "STRING"},
            "clues": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "kind": {"type": "STRING"},
                        "observation": {"type": "STRING"},
                        "geographicImplication": {"type": "STRING"},
                        "strength": {"type": "STRING"},
                    },
                    "required": ["kind", "observation", "geographicImplication", "strength"],
                },
            },
            "searchableNames": {"type": "ARRAY", "items": {"type": "STRING"}},
            "explanation": {"type": "STRING"},
        },
        "required": ["imageType", "regionHints", "clues", "searchableNames", "explanation"],
    }
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": 0.1,
        },
    }
    last_err = "no model"
    async with httpx.AsyncClient(timeout=httpx.Timeout(22.0, connect=8.0)) as client:
        for model in MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                r = await client.post(
                    url,
                    headers={"Content-Type": "application/json", "x-goog-api-key": key},
                    json=body,
                )
            except Exception as e:
                last_err = str(e)[:180]
                continue
            if r.status_code == 404:
                last_err = f"{model} not found"
                continue
            if r.status_code >= 400:
                last_err = f"{model} HTTP {r.status_code}: {r.text[:180]}"
                if r.status_code == 403:
                    return {"ok": False, "error": last_err}
                if r.status_code == 429:
                    await asyncio.sleep(0.8)
                continue
            try:
                data = r.json()
                text = "".join(
                    p.get("text") or ""
                    for p in (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
                )
                obj = json.loads(text or "{}")
            except Exception as e:
                last_err = f"parse: {e}"
                continue
            obj = _strip_coords(obj)
            obj["ok"] = True
            obj["model"] = model
            return obj
    return {"ok": False, "error": last_err}
