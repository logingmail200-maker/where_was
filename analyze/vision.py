from __future__ import annotations

import json
import os
import re
import asyncio
from pathlib import Path

import httpx

MODELS = tuple(
    x.strip()
    for x in os.environ.get("GEMINI_MODELS", "gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-3.6-flash").split(",")
    if x.strip()
)

PROMPT = """You are a forensic visual geolocation analyst. You receive the full scene followed by overlapping high-resolution crops of the SAME image. Inspect every crop independently, then reconcile repeated readings. Do not invent unread text. Never output coordinates, plus codes, or a street address you cannot read.

Image order: {image_labels}

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
- textReads: every useful visible text fragment as {{text, kind, confidence, imageIndex}}. kind is business | road | locality | transit | phone | institution | other. confidence is 0 to 1.
- searchQueries: up to 6 concise place-search queries built only from visible text and supported region hints. Prefer business + road + city, phone number, or two businesses in one scene. Never add an unobserved place.
- landmarkHypotheses: up to 3 possible known places as {{name, region, confidence, reasons}}. Use visual recognition here when exact lettering is ambiguous. Keep confidence below 0.7 unless multiple independent visual details match. Hypotheses are for candidate discovery, not proof.
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


def _ambiguous(obj: dict) -> bool:
    if obj.get("searchableNames"):
        return False
    text_conf = max((float(row.get("confidence") or 0.0) for row in obj.get("textReads") or []), default=0.0)
    hypothesis_conf = max((float(row.get("confidence") or 0.0) for row in obj.get("landmarkHypotheses") or []), default=0.0)
    return text_conf < 0.90 and hypothesis_conf < 0.75


def _merge_responses(responses: list[dict]) -> dict:
    """Keep independent model reads visible instead of allowing one guess to win."""
    if len(responses) == 1:
        return responses[0]
    first = dict(responses[0])
    for key, limit in (("regionHints", 8), ("languagesSeen", 8), ("searchableNames", 12), ("searchQueries", 10)):
        first[key] = list(dict.fromkeys(
            str(value).strip()
            for response in responses
            for value in (response.get(key) or [])
            if str(value).strip()
        ))[:limit]
    first["clues"] = [clue for response in responses for clue in (response.get("clues") or [])][:24]
    reads: dict[tuple[str, str], dict] = {}
    for response in responses:
        for row in response.get("textReads") or []:
            key = (re.sub(r"\W+", "", str(row.get("text") or "").lower()), str(row.get("kind") or ""))
            if not key[0]:
                continue
            current = reads.get(key)
            if current is None or float(row.get("confidence") or 0) > float(current.get("confidence") or 0):
                reads[key] = row
    first["textReads"] = list(reads.values())[:40]
    hypotheses: dict[str, dict] = {}
    for response in responses:
        for row in response.get("landmarkHypotheses") or []:
            key = re.sub(r"\W+", "", str(row.get("name") or "").lower())
            if not key:
                continue
            current = hypotheses.get(key)
            if current is None or float(row.get("confidence") or 0) > float(current.get("confidence") or 0):
                hypotheses[key] = row
    first["landmarkHypotheses"] = sorted(
        hypotheses.values(), key=lambda row: -float(row.get("confidence") or 0)
    )[:5]
    first["model"] = "+".join(str(response.get("model") or "") for response in responses)
    first["explanation"] = " ".join(
        str(response.get("explanation") or "").strip() for response in responses if response.get("explanation")
    )[:500]
    return first


async def ask_gemini(images: bytes | list[tuple[str, bytes]], ocr_text: str) -> dict:
    key = load_gemini_key()
    if not key:
        return {"ok": False, "error": "GEMINI_API_KEY not set", "skipped": True}

    import base64

    if isinstance(images, (bytes, bytearray)):
        images = [("full scene", bytes(images))]
    image_labels = "; ".join(f"{index}: {label}" for index, (label, _) in enumerate(images, 1))
    prompt = PROMPT.format(ocr=json.dumps((ocr_text or "")[:5000]), image_labels=image_labels)
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
            "textReads": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {"type": "STRING"},
                        "kind": {"type": "STRING"},
                        "confidence": {"type": "NUMBER"},
                        "imageIndex": {"type": "INTEGER"},
                    },
                    "required": ["text", "kind", "confidence", "imageIndex"],
                },
            },
            "searchQueries": {"type": "ARRAY", "items": {"type": "STRING"}},
            "landmarkHypotheses": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING"},
                        "region": {"type": "STRING"},
                        "confidence": {"type": "NUMBER"},
                        "reasons": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["name", "region", "confidence", "reasons"],
                },
            },
            "explanation": {"type": "STRING"},
        },
        "required": ["imageType", "regionHints", "clues", "searchableNames", "textReads", "searchQueries", "landmarkHypotheses", "explanation"],
    }
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    *[
                        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(data).decode("ascii")}}
                        for _, data in images
                    ],
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
    successful: list[dict] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(24.0, connect=8.0)) as client:
        for model in MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            r = None
            for attempt in range(2):
                try:
                    r = await client.post(
                        url,
                        headers={"Content-Type": "application/json", "x-goog-api-key": key},
                        json=body,
                    )
                except Exception as e:
                    last_err = str(e)[:180]
                    if attempt == 0:
                        await asyncio.sleep(1.2)
                    continue
                if r.status_code not in {429, 500, 502, 503, 504}:
                    break
                last_err = f"{model} HTTP {r.status_code}: {r.text[:180]}"
                if attempt == 0:
                    await asyncio.sleep(1.4)
            if r is None:
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
            obj["textReads"] = [
                row for row in (obj.get("textReads") or [])
                if isinstance(row, dict)
                and 1 <= len(str(row.get("text") or "").strip()) <= 100
                and 0.0 <= float(row.get("confidence") or 0.0) <= 1.0
            ][:40]
            obj["searchQueries"] = [
                str(q).strip() for q in (obj.get("searchQueries") or [])
                if 4 <= len(str(q).strip()) <= 140
            ][:6]
            obj["landmarkHypotheses"] = [
                row for row in (obj.get("landmarkHypotheses") or [])
                if isinstance(row, dict)
                and 4 <= len(str(row.get("name") or "").strip()) <= 100
                and 0.0 <= float(row.get("confidence") or 0.0) <= 1.0
            ][:3]
            obj["ok"] = True
            obj["model"] = model
            successful.append(obj)
            if not _ambiguous(obj) or len(successful) >= 2:
                return _merge_responses(successful)
    if successful:
        return _merge_responses(successful)
    return {"ok": False, "error": last_err}
