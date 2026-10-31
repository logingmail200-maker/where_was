from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus


COORDS = re.compile(r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)")
AT_COORDS = re.compile(r"/@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)")


def _queries(mind: dict) -> list[str]:
    city = (mind.get("city") or "").strip()
    brands = [b.get("label") for b in mind.get("brands") or [] if b.get("label")]
    # search_names includes high-value OCR readings as well as vision readings.
    # This matters for small local stores that a vision model may not recognise
    # as a known brand but that Maps can resolve precisely.
    visual = [str(x).strip() for x in mind.get("search_names") or mind.get("visual_names") or [] if str(x).strip()]
    phones = [str(x).strip() for x in mind.get("phones") or [] if str(x).strip()]
    roads = [x for x in visual if re.search(r"\b(road|rd|street|st|main|avenue|ave|highway|hwy)\b", x, re.I)]
    businesses = [x for x in visual if x not in roads and len(x) >= 5]
    out = []
    out.extend(str(q) for q in (mind.get("model_queries") or [])[:4])
    out.extend(f"{phone} {city}" for phone in phones[:2])
    # Road + visible business is normally the strongest searchable combination.
    for road in roads[:2]:
        for brand in brands[:2]:
            out.append(f"{brand} {road} {city}")
        for business in businesses[:2]:
            out.append(f"{business} {road} {city}")
    # Search independently read storefronts so nearby results can corroborate
    # one another instead of trusting one chain branch.
    out.extend(f"{name} {city}" for name in businesses[:4])
    for left in businesses[:3]:
        for right in businesses[:3]:
            if left != right:
                out.append(f"{left} near {right} {city}")
    if len(brands) >= 2:
        out.extend(f"{brand} {city}" for brand in brands[:2])
    seen, clean = set(), []
    for q in out:
        q = re.sub(r"\s+", " ", q).strip()
        key = q.lower()
        if len(q) < 6 or key in seen:
            continue
        seen.add(key)
        clean.append(q)
        if len(clean) >= 9:
            break
    return clean


async def _search_one(context, query: str, mind: dict) -> list[dict]:
    """Resolve one Maps query on its own page so clue searches can overlap."""
    page = await context.new_page()
    page.set_default_timeout(4200)
    hits: list[dict] = []
    try:
        url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"
        await page.goto(url, wait_until="domcontentloaded", timeout=6500)
        # Maps renders the first result quickly; a short bounded pause is much
        # faster than serially waiting on every clue query.
        await page.wait_for_timeout(650)
        direct_match = COORDS.search(page.url) or AT_COORDS.search(page.url)
        if direct_match:
            lat, lon = float(direct_match.group(1)), float(direct_match.group(2))
            title = (await page.title()).replace(" - Google Maps", "").strip()
            heading = page.locator("h1").first
            if await heading.count():
                try:
                    title = (await heading.inner_text()).strip() or title
                except Exception:
                    pass
            address = ""
            address_node = page.locator('[data-item-id="address"], button[data-item-id*="address"]').first
            if await address_node.count():
                try:
                    address = (await address_node.get_attribute("aria-label") or await address_node.inner_text()).strip()
                except Exception:
                    pass
            if title and title.lower() not in {"google maps", "maps"}:
                hits.append({
                    "source": "google-maps", "query": query, "name": title,
                    "address": address or None, "lat": lat, "lon": lon,
                    "osm_id": f"g-{lat:.6f}-{lon:.6f}", "importance": 0.62,
                    "query_strength": _query_strength(query, mind),
                })
        links = page.locator('a[href*="/maps/place/"]')
        for index in range(min(await links.count(), 5)):
            link = links.nth(index)
            href = await link.get_attribute("href") or ""
            match = COORDS.search(href)
            if not match:
                continue
            lat, lon = float(match.group(1)), float(match.group(2))
            name = (await link.get_attribute("aria-label") or await link.inner_text() or "Place").strip()
            article = link.locator("xpath=ancestor::div[@role='article'][1]")
            address = ""
            if await article.count():
                address = " · ".join((await article.first.inner_text()).splitlines()[1:5])[:300]
            query_phones = re.findall(r"\b\d{10}\b", re.sub(r"[^\d ]", " ", query))
            result_digits = re.sub(r"\D", "", f"{name} {address}")
            if query_phones and not any(phone in result_digits for phone in query_phones):
                continue
            hits.append({
                "source": "google-maps", "query": query, "name": name,
                "address": address or None, "lat": lat, "lon": lon,
                "osm_id": f"g-{lat:.6f}-{lon:.6f}", "importance": 0.55,
                "query_strength": _query_strength(query, mind),
            })
    except Exception as exc:
        print(f"Google Maps query failed: {type(exc).__name__}: {exc}")
        return []
    finally:
        await page.close()
    return hits


async def google_place_candidates(mind: dict) -> list[dict]:
    """Optional browser fallback for POIs missing from the open map index."""
    queries = _queries(mind)
    if not queries:
        return []
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        # Make missing browser libraries visible in production instead of
        # silently degrading to an empty shortlist.
        print(f"Google Maps candidate lookup unavailable: {type(exc).__name__}: {exc}")
        return []

    hits, seen = [], set()
    try:
        async with async_playwright() as pw:
            launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            try:
                browser = await pw.chromium.launch(channel="chrome", **launch_args)
            except Exception:
                browser = await pw.chromium.launch(**launch_args)
            context = await browser.new_context(
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1180, "height": 760},
            )
            # Run the strongest clue combinations concurrently. Six pages are
            # still a small, bounded lookup and cut the resolver from ~19 s to
            # the duration of the slowest query.
            # Resolve the primary visible business alone first. Maps can drop
            # this high-value query when several tabs start simultaneously.
            brands = [b.get("label") for b in mind.get("brands") or [] if b.get("label")]
            city = str(mind.get("city") or "").strip()
            primary_query = next(
                (q for q in queries if brands and q.lower() == f"{brands[0]} {city}".strip().lower()),
                None,
            )
            ordered_groups = []
            if primary_query:
                ordered_groups.append([primary_query])
            ordered_groups.append([q for q in queries if q != primary_query][:5])
            for group_index, query_group in enumerate(ordered_groups):
                if not query_group:
                    continue
                if group_index == 0:
                    batches = await asyncio.gather(
                        *(_search_one(context, query, mind) for query in query_group),
                        return_exceptions=True,
                    )
                else:
                    jobs = [asyncio.create_task(_search_one(context, query, mind)) for query in query_group]
                    done, pending = await asyncio.wait(jobs, timeout=6.5)
                    batches = [job.result() for job in done if not job.cancelled() and job.exception() is None]
                    for job in pending:
                        job.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                for batch in batches:
                    if isinstance(batch, list):
                        for hit in batch:
                            key = (round(hit["lat"], 6), round(hit["lon"], 6), str(hit.get("query") or "").lower())
                            if key not in seen:
                                seen.add(key)
                                hits.append(hit)
            await browser.close()
    except Exception:
        # Maps is an optional precision source. OpenStreetMap/Photon results
        # remain available and the request must never fail because Chromium is
        # unavailable on a constrained host.
        return []
    preferred = [b.get("label") for b in mind.get("brands") or [] if b.get("label")]
    preferred.extend(mind.get("search_names") or [])
    return _cluster_hits(hits, preferred)[:20]


def _cluster_hits(hits: list[dict], preferred_names: list[str] | None = None) -> list[dict]:
    """Merge repeated searches that resolve to the same physical candidate."""
    groups: list[list[dict]] = []
    for hit in hits:
        group = next(
            (
                group for group in groups
                if abs(group[0]["lat"] - hit["lat"]) < 0.0008
                and abs(group[0]["lon"] - hit["lon"]) < 0.0008
            ),
            None,
        )
        if group is None:
            groups.append([hit])
        else:
            group.append(hit)
    merged = []
    for group in groups:
        best = max(group, key=lambda row: float(row.get("query_strength") or 0.0))
        for preferred in preferred_names or []:
            compact = re.sub(r"[^a-z0-9]+", "", str(preferred).lower())
            named = next(
                (
                    row for row in group
                    if compact and compact in re.sub(r"[^a-z0-9]+", "", str(row.get("name") or "").lower())
                ),
                None,
            )
            if named is not None:
                best = named
                break
        queries = list(dict.fromkeys(row.get("query") for row in group if row.get("query")))
        sources = list(dict.fromkeys(row.get("source") for row in group if row.get("source")))
        nearby_names = list(dict.fromkeys(row.get("name") for row in group if row.get("name")))
        merged.append(
            {
                **best,
                "matched_queries": queries,
                "support_count": len(queries),
                "support_sources": sources,
                "nearby_names": nearby_names,
                "query_strength": min(1.0, float(best.get("query_strength") or 0.0) + 0.07 * max(0, len(queries) - 1)),
            }
        )
    merged.sort(key=lambda row: (-int(row.get("support_count") or 1), -float(row.get("query_strength") or 0.0)))
    return merged


def _query_strength(query: str, mind: dict) -> float:
    low = query.lower()
    if any(phone in low for phone in mind.get("phones") or []):
        return 1.0
    if re.search(r"\b(road|rd|street|st|main|avenue|ave|highway|hwy)\b", low):
        return 0.9
    labels = [str(b.get("label") or "").lower() for b in mind.get("brands") or []]
    if sum(1 for label in labels if label and label in low) >= 2:
        return 0.8
    # A specific local name plus a city is enough to make a useful candidate
    # query, but not enough by itself to declare a photo an exact match.
    searchable = [str(x).lower() for x in mind.get("search_names") or []]
    if sum(1 for name in searchable if len(name) >= 5 and name in low) >= 2:
        return 0.92
    if any(name in low for name in searchable if len(name) >= 5):
        return 0.68
    return 0.45


async def bounded_google_place_candidates(mind: dict) -> list[dict]:
    try:
        return await asyncio.wait_for(google_place_candidates(mind), timeout=32.0)
    except (asyncio.TimeoutError, Exception):
        return []
