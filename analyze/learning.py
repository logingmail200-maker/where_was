from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "learning.db"
MODEL_PATH = DATA / "ranker.json"
LOCK = threading.Lock()

FEATURES = [
    "bias", "base_score", "has_address", "small_radius", "why_count",
    "tier_pin", "tier_block", "source_exif", "source_ocr", "source_scene",
    "source_nominatim", "source_overpass", "second_clue", "independent",
    "unique_name", "chain_warning", "infrastructure",
]


def candidate_features(candidate: dict) -> dict[str, float]:
    source = str(candidate.get("source") or "").lower()
    tier = str(candidate.get("tier") or "").lower()
    why = " ".join(str(x) for x in candidate.get("why") or []).lower()
    radius = max(1.0, float(candidate.get("radius_m") or 1000))
    return {
        "bias": 1.0,
        "base_score": float(candidate.get("base_score", candidate.get("score") or 0)) / 100.0,
        "has_address": 1.0 if candidate.get("address") else 0.0,
        "small_radius": min(1.0, 150.0 / radius),
        "why_count": min(1.0, len(candidate.get("why") or []) / 4.0),
        "tier_pin": 1.0 if tier == "pin" else 0.0,
        "tier_block": 1.0 if tier == "block" else 0.0,
        "source_exif": 1.0 if source == "exif" else 0.0,
        "source_ocr": 1.0 if source == "ocr-coords" else 0.0,
        "source_scene": 1.0 if source == "scene" else 0.0,
        "source_nominatim": 1.0 if source == "nominatim" else 0.0,
        "source_overpass": 1.0 if source == "overpass" else 0.0,
        "second_clue": 1.0 if "second" in why or "nearby name" in why else 0.0,
        "independent": 1.0 if "independent" in why else 0.0,
        "unique_name": 1.0 if "unique" in why or "distinctive" in why else 0.0,
        "chain_warning": 1.0 if "chain" in why or "multiple distant" in why else 0.0,
        "infrastructure": 1.0 if any(x in why for x in ("flyover", "viaduct", "metro", "infrastructure")) else 0.0,
    }


def _connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """CREATE TABLE IF NOT EXISTS examples (
        analysis_id TEXT NOT NULL, candidate_id TEXT NOT NULL, label INTEGER NOT NULL,
        features TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (analysis_id, candidate_id))"""
    )
    return db


def _read_model() -> dict:
    if not MODEL_PATH.exists():
        return {"weights": {k: 0.0 for k in FEATURES}, "sessions": 0, "examples": 0}
    try:
        return json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"weights": {k: 0.0 for k in FEATURES}, "sessions": 0, "examples": 0}


def learning_status() -> dict:
    model = _read_model()
    return {"sessions": int(model.get("sessions") or 0), "examples": int(model.get("examples") or 0), "active": int(model.get("sessions") or 0) >= 3}


def learned_score(candidate: dict) -> float:
    model = _read_model()
    if int(model.get("sessions") or 0) < 3:
        return float(candidate.get("score") or 0)
    features = candidate_features(candidate)
    weights = model.get("weights") or {}
    z = sum(features.get(k, 0.0) * float(weights.get(k, 0.0)) for k in FEATURES)
    probability = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, z))))
    # Preserve useful deterministic evidence while allowing feedback to reorder
    # close candidates. A learner cannot overpower embedded coordinates.
    base = float(candidate.get("score") or 0)
    blend = 0.12 if candidate.get("source") in {"exif", "ocr-coords"} else 0.38
    return (1.0 - blend) * base + blend * probability * 100.0


def record_feedback(analysis_id: str, candidates: list[dict], selected_id: str) -> dict:
    if not analysis_id or not candidates or not selected_id:
        raise ValueError("Missing feedback data")
    ids = {str(c.get("id")) for c in candidates}
    if selected_id not in ids:
        raise ValueError("Selected location is not part of this result")
    clean = candidates[:8]
    with LOCK:
        db = _connect()
        try:
            for candidate in clean:
                db.execute(
                    "INSERT OR REPLACE INTO examples (analysis_id,candidate_id,label,features) VALUES (?,?,?,?)",
                    (
                        analysis_id,
                        str(candidate.get("id")),
                        1 if str(candidate.get("id")) == selected_id else 0,
                        json.dumps(candidate_features(candidate), separators=(",", ":")),
                    ),
                )
            db.commit()
            _train(db)
        finally:
            db.close()
    return learning_status()


def _train(db: sqlite3.Connection) -> None:
    rows = list(db.execute("SELECT label, features FROM examples ORDER BY created_at, analysis_id, candidate_id"))
    sessions = int(db.execute("SELECT COUNT(DISTINCT analysis_id) FROM examples").fetchone()[0])
    weights = {k: 0.0 for k in FEATURES}
    if rows:
        # Small, deterministic logistic ranker. Rebuilding from stored examples
        # makes corrections reproducible and avoids accumulating numerical drift.
        for epoch in range(180):
            rate = 0.12 / (1.0 + epoch * 0.018)
            for label, raw in rows:
                x = json.loads(raw)
                z = sum(weights[k] * float(x.get(k, 0.0)) for k in FEATURES)
                pred = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, z))))
                for key in FEATURES:
                    regularization = 0.001 * weights[key] if key != "bias" else 0.0
                    weights[key] += rate * ((float(label) - pred) * float(x.get(key, 0.0)) - regularization)
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"weights": weights, "sessions": sessions, "examples": len(rows)}, indent=2), encoding="utf-8")
    tmp.replace(MODEL_PATH)
