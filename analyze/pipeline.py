from __future__ import annotations

import asyncio
import json
import time
import traceback
from pathlib import Path

from .exif_tools import classify_image, jpeg_bytes_for_search, jpeg_vision_bundle, read_exif
from .ocr_tools import apply_gemini_signs, run_ocr
from .places import bbox_around, nearby_elevated, overpass_named, pair_names, reverse_geocode
from .rank import rank
from .think import compose, hunt
from .reverse import reverse_search
from .vision import ask_gemini
from .google_places import bounded_google_place_candidates
from .street_sources import attach_free_street_sources


def _event(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


async def analyze_stream(path: Path, filename: str, analysis_id: str | None = None):
    t0 = time.time()
    try:
        yield _event({"type": "step", "id": "read", "status": "run", "label": "Reading the file"})
        exif = await asyncio.to_thread(read_exif, path)
        kind = await asyncio.to_thread(classify_image, path, exif)
        yield _event(
            {
                "type": "step",
                "id": "read",
                "status": "done",
                "label": "File",
                "detail": f"{kind['kind']} · {kind.get('width')}×{kind.get('height')}",
            }
        )

        gps = exif.get("gps")
        if gps:
            yield _event({"type": "step", "id": "exif", "status": "done", "label": "EXIF GPS", "detail": f"{gps['lat']:.5f}, {gps['lon']:.5f}"})
        else:
            yield _event({"type": "step", "id": "exif", "status": "done", "label": "EXIF", "detail": "No GPS in file"})

        yield _event({"type": "step", "id": "ocr", "status": "run", "label": "Reading visible text"})
        yield _event({"type": "step", "id": "rev", "status": "run", "label": "Matching image sources"})
        yield _event({"type": "step", "id": "vision", "status": "run", "label": "Understanding the scene"})

        jpeg = await asyncio.to_thread(jpeg_bytes_for_search, path)

        async def do_ocr():
            return await asyncio.to_thread(run_ocr, path)

        async def do_rev():
            return await reverse_search(jpeg)

        async def do_revgeo():
            if gps:
                return await reverse_geocode(gps["lat"], gps["lon"])
            return None

        ocr_task = asyncio.create_task(do_ocr())
        rev_task = asyncio.create_task(do_rev())
        geo_task = asyncio.create_task(do_revgeo())
        ocr_r = await ocr_task
        ocr_text_for_vision = "" if isinstance(ocr_r, Exception) else (ocr_r.get("text") or "")
        vision_bundle = await asyncio.to_thread(jpeg_vision_bundle, path)
        vis_task = asyncio.create_task(ask_gemini(vision_bundle, ocr_text_for_vision))
        rev_r, geo_r, vis_r = await asyncio.gather(rev_task, geo_task, vis_task, return_exceptions=True)
        if isinstance(ocr_r, Exception):
            ocr = {"text": "", "lines": [], "plates": [], "coordinates": [], "plus_codes": [], "names": [], "urls": []}
            ocr_err = str(ocr_r)
        else:
            ocr, ocr_err = ocr_r, None
        if isinstance(rev_r, Exception):
            rev = {"engines": [], "guesses": [], "pages": [], "errors": [str(rev_r)]}
        else:
            rev = rev_r
        if isinstance(geo_r, Exception):
            geo_r = None
        if isinstance(vis_r, Exception):
            visual = {"ok": False, "error": str(vis_r), "clues": [], "searchableNames": [], "regionHints": []}
        else:
            visual = vis_r or {}

        if visual.get("ok"):
            ocr = apply_gemini_signs(ocr, visual)
        ocr_preview = (ocr.get("text") or "").replace("\n", " · ")[:180]
        yield _event(
            {
                "type": "step",
                "id": "ocr",
                "status": "done",
                "label": "Text signals",
                "detail": ocr_preview or ("failed: " + (ocr_err or "no text")),
            }
        )
        rev_detail = (
            f"{len(rev.get('pages') or [])} pages · {len(rev.get('guesses') or [])} guesses"
            if rev.get("engines")
            else ("blocked: " + "; ".join(rev.get("errors") or ["no engine"]))
        )
        yield _event({"type": "step", "id": "rev", "status": "done", "label": "Reverse search", "detail": rev_detail})
        if visual.get("ok"):
            nclues = len(visual.get("clues") or [])
            yield _event(
                {
                    "type": "step",
                    "id": "vision",
                    "status": "done",
                    "label": "Visual signals",
                    "detail": f"{visual.get('model')} · {nclues} clues · {', '.join((visual.get('regionHints') or [])[:3]) or 'no region'}",
                }
            )
        else:
            yield _event(
                {
                    "type": "step",
                    "id": "vision",
                    "status": "done",
                    "label": "Visual signals",
                    "detail": visual.get("error") or "unavailable",
                }
            )

        mind = compose(ocr, visual, exif, kind, rev)
        names = mind.get("names") or []
        # A vision read of a local sign is allowed to create a *shortlist*
        # query. It still has to resolve to a real mapped place and is never
        # presented as an independently verified camera position.
        rank_names = list(dict.fromkeys(names + (mind.get("search_names") or [])))
        infra = mind.get("infra") or []
        thought = (mind.get("thoughts") or ["Linking OCR with Gemini…"])[0]
        yield _event(
            {
                "type": "step",
                "id": "places",
                "status": "run",
                "label": "Interrelating clues",
                "detail": thought,
            }
        )
        google_task = asyncio.create_task(bounded_google_place_candidates(mind))
        hunt_task = asyncio.create_task(asyncio.wait_for(hunt(mind, gps), timeout=18.0))
        hunted_r, google_r = await asyncio.gather(hunt_task, google_task, return_exceptions=True)
        hunted = (
            hunted_r
            if isinstance(hunted_r, dict)
            else {"scene": {"hits": [], "pairs": [], "groups": []}, "nominatim": []}
        )
        google_hits = google_r if isinstance(google_r, list) else []
        scene = hunted.get("scene") or {"hits": [], "pairs": [], "groups": []}

        nominatim: list = list(hunted.get("nominatim") or []) + list(google_hits or [])
        overpass: list = list(scene.get("hits") or [])
        queries = mind.get("distinctive") or []

        bbox = None
        if gps:
            bbox = bbox_around(gps["lat"], gps["lon"], 12)
        elif scene.get("bbox"):
            bbox = scene["bbox"]
        elif nominatim:
            bbox = bbox_around(nominatim[0]["lat"], nominatim[0]["lon"], 25)
        if queries and bbox and not scene.get("hits"):
            try:
                overpass = await overpass_named(queries[:2], bbox)
            except Exception:
                overpass = []

        # Pair across all candidate sources; the second visible sign may come
        # from Nominatim while the primary brand came from Overpass.
        pairs = list(scene.get("pairs") or []) + pair_names(overpass + nominatim)
        elevated: list = list(scene.get("elevated") or [])
        seed_pts = (overpass + nominatim)[:8]
        if gps:
            seed_pts = [{"lat": gps["lat"], "lon": gps["lon"], "name": "exif"}] + seed_pts
        if seed_pts and infra and not elevated:
            try:
                elevated = await nearby_elevated(seed_pts)
            except Exception:
                elevated = []

        yield _event(
            {
                "type": "step",
                "id": "places",
                "status": "done",
                "label": "Map match",
                "detail": f"{len(nominatim)} Nominatim · {len(overpass)} OSM · {len(pairs)} pairs",
            }
        )

        yield _event({"type": "step", "id": "rank", "status": "run", "label": "Ranking candidates"})
        ranked = rank(
            exif_gps=gps,
            gps_error_m=exif.get("gps_error_m"),
            reverse_place=geo_r if isinstance(geo_r, dict) else None,
            ocr_coords=ocr.get("coordinates") or [],
            plates=ocr.get("plates") or [],
            nominatim=nominatim,
            overpass=overpass,
            reverse_pages=rev.get("pages") or [],
            reverse_guesses=rev.get("guesses") or [],
            names=rank_names,
            kind=kind["kind"],
            pairs=pairs,
            elevated=elevated,
            infra_words=infra,
            regional_hypotheses=mind.get("regional_hypotheses") or [],
        )
        # Query free, crowdsourced street-imagery only after the candidate
        # shortlist exists. This avoids a costly/unbounded worldwide scan.
        await attach_free_street_sources(ranked["candidates"])
        icon = {
            "architecture": "⌂",
            "vegetation": "♧",
            "signage": "▤",
            "brand": "◈",
            "infrastructure": "▦",
            "vehicle": "▣",
            "terrain": "▲",
            "road furniture": "┊",
        }
        for c in visual.get("clues") or []:
            ranked["clues"].append(
                {
                    "icon": icon.get((c.get("kind") or "").lower(), "◈"),
                    "title": f"Visual clue · {c.get('kind')} · {c.get('strength')}",
                    "body": f"{c.get('observation')} → {c.get('geographicImplication')}",
                }
            )
        for t in reversed(mind.get("thoughts") or []):
            ranked["clues"].insert(0, {"icon": "🧠", "title": "How it linked the clues", "body": t})
        if visual.get("explanation"):
            ranked["clues"].append({"icon": "◈", "title": "Scene summary", "body": visual["explanation"]})
        if visual.get("drivingSide") and visual.get("drivingSide") != "unclear":
            ranked["clues"].append(
                {
                    "icon": "┊",
                    "title": f"Driving side · {visual.get('drivingSide')}",
                    "body": "Visual estimate only; confirm on Street View.",
                }
            )
        elapsed = int((time.time() - t0) * 1000)

        def aspect(aid, label, state, detail):
            return {"id": aid, "label": label, "state": state, "detail": detail}

        aspects = [
            aspect("file", "File type", "hit", f"{kind['kind']} · {kind.get('width')}×{kind.get('height')}"),
            aspect("gps", "EXIF GPS", "hit" if gps else "empty", f"{gps['lat']:.5f}, {gps['lon']:.5f}" if gps else "No GPS tag"),
            aspect("camera", "Camera", "hit" if exif.get("make") else "empty", " ".join(filter(None, [exif.get("make"), exif.get("model")])) or "None"),
            aspect("time", "Capture time", "hit" if exif.get("datetime") else "empty", exif.get("datetime") or "None"),
            aspect("ocr", "Visible text", "hit" if ocr.get("text") else "empty", (ocr.get("text") or "No text")[:120]),
            aspect("plates", "Number plate", "hit" if ocr.get("plates") else "empty", ", ".join(p["plate"] + " → " + p["region"] for p in ocr.get("plates") or []) or "None"),
            aspect("coords", "Printed coordinates", "hit" if ocr.get("coordinates") else "empty", str((ocr.get("coordinates") or [{}])[0].get("raw") if ocr.get("coordinates") else "None")),
            aspect(
                "vision",
                "Visual analysis",
                "hit" if visual.get("ok") else ("fail" if visual.get("error") else "empty"),
                (visual.get("explanation") or visual.get("error") or "off")[:120],
            ),
            aspect(
                "environment",
                "Environmental profile",
                "hit" if visual.get("environmentProfile") else "empty",
                " · ".join(str(value) for value in (visual.get("environmentProfile") or {}).values() if value)[:160] or "None",
            ),
            aspect("rev", "Reverse search", "hit" if rev.get("pages") else ("fail" if rev.get("errors") else "empty"), f"{len(rev.get('pages') or [])} pages · engines: {', '.join(rev.get('engines') or []) or 'none'}"),
            aspect("think", "Linked reasoning", "hit" if mind.get("thoughts") else "empty", (mind.get("thoughts") or [""])[0][:160]),
            aspect("city", "Locked city", "hit" if mind.get("city") else "empty", mind.get("city") or "None"),
            aspect("names", "Distinctive names", "hit" if names else "empty", ", ".join(names[:6]) or "None"),
            aspect("osm", "OpenStreetMap", "hit" if (nominatim or overpass) else "empty", f"{len(nominatim)+len(overpass)} hits"),
            aspect(
                "scene",
                "Brand ∩ city ∩ flyover",
                "hit" if scene.get("hits") else "empty",
                (
                    f"{scene.get('city')} · "
                    + ", ".join(f"{g['brand']}×{g['n']}" for g in scene.get("groups") or [])
                    + (f" · {len(scene.get('pairs') or [])} close pairs" if scene.get("pairs") else "")
                )[:160]
                if scene.get("hits") or scene.get("groups")
                else "Need a city plus two shop names (e.g. Kalyan near Titan under a metro)",
            ),
            aspect("pair", "Two-clue match", "hit" if pairs else "empty", f"{len(pairs)} nearby name pairs" if pairs else "Need two different names close on the map"),
            aspect("sv", "Street View", "hit" if ranked["candidates"] else "empty", "Button on each pin — not a worldwide scan"),
        ]
        result = {
            "analysis_id": analysis_id,
            "filename": filename,
            "kind": kind["kind"],
            "kind_reasons": kind.get("reasons") or [],
            "exif": {
                "make": exif.get("make"),
                "model": exif.get("model"),
                "datetime": exif.get("datetime"),
                "gps": gps,
            },
            "ocr_text": ocr.get("text") or "",
            "plates": ocr.get("plates") or [],
            "visual": {
                "ok": bool(visual.get("ok")),
                "model": visual.get("model"),
                "imageType": visual.get("imageType"),
                "depictedPlaceVsCapture": visual.get("depictedPlaceVsCapture"),
                "regionHints": visual.get("regionHints") or [],
                "languagesSeen": visual.get("languagesSeen") or [],
                "drivingSide": visual.get("drivingSide"),
                "searchableNames": visual.get("searchableNames") or [],
                "textReads": visual.get("textReads") or [],
                "searchQueries": visual.get("searchQueries") or [],
                "landmarkHypotheses": visual.get("landmarkHypotheses") or [],
                "environmentProfile": visual.get("environmentProfile") or {},
                "regionalHypotheses": visual.get("regionalHypotheses") or [],
                "clues": visual.get("clues") or [],
                "explanation": visual.get("explanation"),
                "error": visual.get("error"),
            },
            "reverse": {
                "engines": rev.get("engines") or [],
                "guesses": rev.get("guesses") or [],
                "pages": rev.get("pages") or [],
                "errors": rev.get("errors") or [],
            },
            "aspects": aspects,
            "candidates": ranked["candidates"],
            "primary": ranked["primary"],
            "clues": ranked["clues"],
            "summary": ranked["summary"],
            "thinking": mind.get("thoughts") or [],
            "elapsed_ms": elapsed,
            "limits": [
                "Street View is opened on the shortlist, not searched across the whole planet.",
                "Reverse-search engines may CAPTCHA; that is not proof the photo is unique.",
                "A chain store without a second clue is not a pin.",
                "Scores are ranking heuristics, not calibrated probabilities.",
            ],
        }
        yield _event({"type": "step", "id": "rank", "status": "done", "label": "Ranked", "detail": ranked["summary"][:120]})
        yield _event({"type": "result", "data": result})
    except Exception as e:
        traceback.print_exc()
        yield _event({"type": "error", "message": str(e)[:300]})
