from __future__ import annotations

import asyncio
import json
import time
import traceback
from pathlib import Path

from .exif_tools import classify_image, jpeg_bytes_for_search, jpeg_vision_bundle, read_exif
from .ocr_tools import apply_gemini_signs, run_ocr
from .places import bbox_around, nearby_elevated, overpass_named, pair_names, reverse_geocode, search_places
from .rank import rank
from .think import compose, hunt
from .reverse import reverse_search
from .vision import ask_gemini, ask_person_guard
from .google_places import bounded_google_place_candidates
from .street_sources import attach_free_street_sources, verify_shortlist_on_demand
from .person_guard import detect_prominent_person
from .retrieval import available_regions, search as search_retrieval


def _event(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


async def analyze_stream(path: Path, filename: str, analysis_id: str | None = None):
    t0 = time.time()
    try:
        yield _event({"type": "step", "id": "read", "status": "run", "label": "Reading the file"})
        # Independent file reads run together.  Previously these were serial,
        # consuming several seconds before any actual search could begin.
        exif_task = asyncio.create_task(asyncio.to_thread(read_exif, path))
        person_task = asyncio.create_task(asyncio.to_thread(detect_prominent_person, path))
        jpeg_task = asyncio.create_task(asyncio.to_thread(jpeg_bytes_for_search, path))
        bundle_task = asyncio.create_task(asyncio.to_thread(jpeg_vision_bundle, path))
        exif, person, jpeg, vision_bundle = await asyncio.gather(
            exif_task, person_task, jpeg_task, bundle_task
        )
        kind = await asyncio.to_thread(classify_image, path, exif)
        if not person.get("available"):
            try:
                person = await asyncio.wait_for(ask_person_guard(jpeg), timeout=5.0)
            except asyncio.TimeoutError:
                person = {"available": False, "prominent": False, "error": "Privacy detector timed out"}
        privacy_limited = bool(person.get("prominent")) or not bool(person.get("available"))
        yield _event(
            {
                "type": "step",
                "id": "read",
                "status": "done",
                "label": "File",
                "detail": f"{kind['kind']} · {kind.get('width')}×{kind.get('height')}",
            }
        )

        if privacy_limited:
            detail = "Prominent person detected — fine street matching is disabled." if person.get("prominent") else "Privacy check unavailable — fine street matching is disabled."
            yield _event({"type": "step", "id": "privacy", "status": "done", "label": "Privacy guard", "detail": detail})
        elif person.get("available"):
            yield _event({"type": "step", "id": "privacy", "status": "done", "label": "Privacy guard", "detail": "No prominent person detected."})
        else:
            yield _event({"type": "step", "id": "privacy", "status": "done", "label": "Privacy guard", "detail": "Detector unavailable; no face crop is retained."})

        gps = exif.get("gps")
        if gps:
            yield _event({"type": "step", "id": "exif", "status": "done", "label": "EXIF GPS", "detail": f"{gps['lat']:.5f}, {gps['lon']:.5f}"})
        else:
            yield _event({"type": "step", "id": "exif", "status": "done", "label": "EXIF", "detail": "No GPS in file"})

        yield _event({"type": "step", "id": "ocr", "status": "run", "label": "Reading visible text"})
        yield _event({"type": "step", "id": "rev", "status": "run", "label": "Matching image sources"})
        yield _event({"type": "step", "id": "vision", "status": "run", "label": "Understanding the scene"})

        async def do_ocr():
            return await asyncio.wait_for(asyncio.to_thread(run_ocr, path), timeout=9.0)

        async def do_rev():
            if privacy_limited:
                return {"engines": [], "guesses": [], "pages": [], "errors": ["Skipped by privacy guard"]}
            return await asyncio.wait_for(reverse_search(jpeg), timeout=11.0)

        async def do_revgeo():
            if gps:
                return await reverse_geocode(gps["lat"], gps["lon"])
            return None

        # Gemini receives the full scene plus sharp crops and performs its own
        # sign reading, so it need not wait for local OCR.  All four evidence
        # branches now start together and reconcile afterwards.
        ocr_task = asyncio.create_task(do_ocr())
        rev_task = asyncio.create_task(do_rev())
        geo_task = asyncio.create_task(do_revgeo())
        vis_task = asyncio.create_task(asyncio.wait_for(ask_gemini(vision_bundle, ""), timeout=14.0))
        ocr_r, rev_r, geo_r, vis_r = await asyncio.gather(
            ocr_task, rev_task, geo_task, vis_task, return_exceptions=True
        )
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
        mind["region_hints"] = visual.get("regionHints") or []
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
        retrieval_task = None
        if not privacy_limited:
            yield _event({"type": "step", "id": "retrieve", "status": "run", "label": "Searching licensed street imagery"})
            retrieval_task = asyncio.create_task(
                asyncio.wait_for(
                    asyncio.to_thread(search_retrieval, path, mind.get("city"), mind=mind, top_k=10),
                    timeout=10.0,
                )
            )

        if privacy_limited:
            # We only resolve an approximate city/region in a personal photo;
            # no business/landmark branch matching or street-image retrieval.
            city_hint = mind.get("city") or next(iter(visual.get("regionHints") or []), None)
            google_task = asyncio.create_task(asyncio.sleep(0, result=[]))
            hunt_task = asyncio.create_task(search_places([city_hint], None, max_queries=1) if city_hint else asyncio.sleep(0, result=[]))
        else:
            async def resolve_places() -> list[dict]:
                # The browser lookup is precise when available. Run a public
                # OpenStreetMap lookup beside it so production still returns a
                # real shortlist when a host cannot launch Chromium.
                brands = [b.get("label") for b in mind.get("brands") or [] if b.get("label")]
                city = mind.get("city")
                open_queries = [f"{brands[0]} {city}" if city else brands[0]] if brands else (mind.get("model_queries") or [])[:1]
                browser_job = asyncio.create_task(
                    asyncio.wait_for(bounded_google_place_candidates(mind), timeout=8.0)
                )
                open_job = asyncio.create_task(search_places(open_queries, city, max_queries=1)) if open_queries else asyncio.create_task(asyncio.sleep(0, result=[]))
                browser_result, open_result = await asyncio.gather(browser_job, open_job, return_exceptions=True)
                precise = browser_result if isinstance(browser_result, list) else []
                fallback = open_result if isinstance(open_result, list) else []
                return precise + fallback

            google_task = asyncio.create_task(asyncio.wait_for(resolve_places(), timeout=11.5))
            hunt_task = asyncio.create_task(asyncio.wait_for(hunt(mind, gps), timeout=8.0))
        gathered = await asyncio.gather(
            hunt_task,
            google_task,
            retrieval_task if retrieval_task else asyncio.sleep(0, result=[]),
            return_exceptions=True,
        )
        hunted_r, google_r, retrieval_r = gathered
        hunted = (
            hunted_r
            if isinstance(hunted_r, dict)
            else {"scene": {"hits": [], "pairs": [], "groups": []}, "nominatim": hunted_r if isinstance(hunted_r, list) else []}
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
                overpass = await asyncio.wait_for(overpass_named(queries[:2], bbox), timeout=1.2)
            except (asyncio.TimeoutError, Exception):
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
                elevated = await asyncio.wait_for(nearby_elevated(seed_pts), timeout=1.2)
            except (asyncio.TimeoutError, Exception):
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

        retrieval_hits: list[dict] = retrieval_r if isinstance(retrieval_r, list) else []
        if not privacy_limited:
            if retrieval_hits:
                shards = len({hit.get("region_shard") for hit in retrieval_hits if hit.get("region_shard")})
                retrieval_detail = f"{len(retrieval_hits)} candidates across {shards or 1} reference region(s)"
            elif isinstance(retrieval_r, Exception):
                retrieval_detail = f"Time-limited: {str(retrieval_r)[:90]}"
            else:
                retrieval_detail = "No matching licensed reference index is installed"
            yield _event({"type": "step", "id": "retrieve", "status": "done", "label": "Licensed street imagery", "detail": retrieval_detail})

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
            retrieval_candidates=retrieval_hits,
        )
        # Query free, crowdsourced street-imagery only after the candidate
        # shortlist exists. This avoids a costly/unbounded worldwide scan.
        if not privacy_limited:
            # Candidate panorama lookup and local verification are independent.
            # Bound them so a slow imagery provider cannot hold the final answer.
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        attach_free_street_sources(ranked["candidates"]),
                        verify_shortlist_on_demand(path, ranked["candidates"]),
                    ),
                    timeout=3.5,
                )
            except (asyncio.TimeoutError, Exception):
                pass
            ranked["candidates"].sort(key=lambda candidate: (-float(candidate.get("score") or 0), candidate.get("radius_m") or 999999))
            ranked["primary"] = next(
                (candidate for candidate in ranked["candidates"] if candidate.get("score", 0) >= 65 and candidate.get("tier") not in {"city", "region"}),
                None,
            )
        else:
            for candidate in ranked["candidates"]:
                candidate["tier"] = "city"
                candidate["radius_m"] = max(25_000, int(candidate.get("radius_m") or 0))
                candidate["score"] = min(55, candidate.get("score") or 55)
                candidate["why"] = ["A prominent person is visible, so this result is intentionally limited to city scale."]
            ranked["primary"] = None
            ranked["summary"] = "A prominent person is visible. Fine-grained place matching was not run; upload a place-only crop for street-level candidates."
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
            "privacy": person,
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
            "reference_coverage": {
                "installed_regions": available_regions(),
                "retrieval_candidates": len(retrieval_hits),
                "note": "Street-level matching depends on licensed reference coverage in the photographed area.",
            },
            "limits": [
                "Only licensed reference imagery is searched; Street View is opened for shortlist inspection, not scraped.",
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
