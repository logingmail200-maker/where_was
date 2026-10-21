from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus


COORDS = re.compile(r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)")


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
    out.extend(f"{phone} {city}" for phone in phones[:2])
    # Road + visible business is normally the strongest searchable combination.
    for road in roads[:2]:
        for brand in brands[:2]:
            out.append(f"{brand} {road} {city}")
    # Search independently read storefronts so nearby results can corroborate
    # one another instead of trusting one chain branch.
    out.extend(f"{name} {city}" for name in businesses[:4])
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
        if len(clean) >= 6:
            break
    return clean


async def google_place_candidates(mind: dict) -> list[dict]:
    """Optional browser fallback for POIs missing from the open map index."""
    queries = _queries(mind)
    if not queries:
        return []
    try:
        from playwright.async_api import async_playwright
    except Exception:
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
            page = await context.new_page()
            page.set_default_timeout(6000)
            for query in queries:
                url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=9000)
                    await page.wait_for_timeout(850)
                    links = page.locator('a[href*="/maps/place/"]')
                    count = min(await links.count(), 5)
                    for index in range(count):
                        link = links.nth(index)
                        href = await link.get_attribute("href") or ""
                        match = COORDS.search(href)
                        if not match:
                            continue
                        lat, lon = float(match.group(1)), float(match.group(2))
                        key = (round(lat, 6), round(lon, 6), query.lower())
                        if key in seen:
                            continue
                        seen.add(key)
                        name = (await link.get_attribute("aria-label") or await link.inner_text() or "Place").strip()
                        article = link.locator("xpath=ancestor::div[@role='article'][1]")
                        address = ""
                        if await article.count():
                            address = " · ".join((await article.first.inner_text()).splitlines()[1:5])[:300]
                        query_phones = re.findall(r"\b\d{10}\b", re.sub(r"[^\d ]", " ", query))
                        result_digits = re.sub(r"\D", "", f"{name} {address}")
                        if query_phones and not any(phone in result_digits for phone in query_phones):
                            continue
                        hits.append(
                            {
                                "source": "google-maps",
                                "query": query,
                                "name": name,
                                "address": address or None,
                                "lat": lat,
                                "lon": lon,
                                "osm_id": f"g-{lat:.6f}-{lon:.6f}",
                                "importance": 0.55,
                                "query_strength": _query_strength(query, mind),
                            }
                        )
                except Exception:
                    continue
            await browser.close()
    except Exception:
        return []
    return hits[:16]


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
    if any(name in low for name in searchable if len(name) >= 5):
        return 0.68
    return 0.45


async def bounded_google_place_candidates(mind: dict) -> list[dict]:
    try:
        return await asyncio.wait_for(google_place_candidates(mind), timeout=18.0)
    except (asyncio.TimeoutError, Exception):
        return []
