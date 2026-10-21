from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SKIP_HOST = (
    "yandex.", "ya.ru", "yastatic.", "bing.com", "bing.net", "microsoft.com",
    "live.com", "google.com", "gstatic.com", "baidu.com", "bdstatic.com",
    "w3.org", "onenote.com", "pinterest.com", "facebook.com", "twitter.com",
    "x.com", "instagram.com", "schema.org", "w3.org",
)


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _useful_link(url: str, title: str) -> bool:
    host = _host(url)
    if not host or any(s in host for s in SKIP_HOST):
        return False
    if len((title or "").strip()) < 4 and not host:
        return False
    return True


async def reverse_search(jpeg: bytes) -> dict:
    """Yandex + Bing over HTTP, then headless Chromium if the HTML was empty/junk."""
    out = {"engines": [], "guesses": [], "pages": [], "errors": []}
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(14.0, connect=6.0), headers=HEADERS) as client:
        yandex = await _yandex(client, jpeg)
        bing = await _bing(client, jpeg)
    for name, block in (("yandex", yandex), ("bing", bing)):
        if block.get("ok"):
            out["engines"].append(name)
            out["guesses"].extend(block.get("guesses") or [])
            out["pages"].extend(block.get("pages") or [])
        elif block.get("error"):
            out["errors"].append(f"{name}: {block['error']}")
    useful = [p for p in out["pages"] if _useful_link(p.get("url") or "", p.get("title") or "")]
    # Browser automation is accurate but slow. Keep it as an opt-in deep pass;
    # normal interactive searches should not wait on it.
    if len(useful) < 2 and os.environ.get("DEEP_REVERSE_SEARCH") == "1":
        pw = await _playwright_yandex(jpeg)
        if pw.get("ok"):
            out["engines"].append("yandex-browser")
            out["guesses"].extend(pw.get("guesses") or [])
            out["pages"].extend(pw.get("pages") or [])
        elif pw.get("error"):
            out["errors"].append("browser: " + pw["error"])
    seen, pages = set(), []
    for p in out["pages"]:
        u = p.get("url") or ""
        if u in seen or not _useful_link(u, p.get("title") or ""):
            continue
        seen.add(u)
        pages.append(p)
    out["pages"] = pages[:20]
    junk = ("bing", "high-quality", "animated gif", "images", "yandex")
    out["guesses"] = list(
        dict.fromkeys(
            g.strip()
            for g in out["guesses"]
            if g and len(g.strip()) > 3 and not any(j in g.lower() for j in junk)
        )
    )[:12]
    return out


async def _yandex(client: httpx.AsyncClient, jpeg: bytes) -> dict:
    try:
        params = {
            "rpt": "imageview",
            "format": "json",
            "request": '{"blocks":[{"block":"b-page_type_search-by-image__link"}]}',
        }
        files = {"upfile": ("photo.jpg", jpeg, "image/jpeg")}
        r = await client.post("https://yandex.com/images/search", params=params, files=files)
        pages, guesses = [], []
        if "application/json" in r.headers.get("content-type", "") or r.text[:1] == "{":
            try:
                data = r.json()
                url_q = None
                for block in data.get("blocks") or []:
                    u = (block.get("params") or {}).get("url")
                    if u:
                        url_q = u
                        break
                if url_q:
                    html_r = await client.get("https://yandex.com/images/search?" + url_q)
                    pages, guesses = _parse_html(html_r.text, "yandex")
            except Exception:
                pages, guesses = _parse_html(r.text, "yandex")
        else:
            pages, guesses = _parse_html(r.text, "yandex")
        if r.status_code >= 400 and not pages:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        return {"ok": True, "pages": pages, "guesses": guesses}
    except Exception as e:
        return {"ok": False, "error": str(e)[:180]}


async def _bing(client: httpx.AsyncClient, jpeg: bytes) -> dict:
    try:
        # Direct-upload used by Bing visual search
        r = await client.post(
            "https://www.bing.com/images/search?view=detailv2&iss=sbiupload&FORM=SBIHMP",
            files={"imageBin": ("photo.jpg", jpeg, "image/jpeg")},
        )
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        pages, guesses = _parse_html(r.text, "bing")
        # Bing often puts a best-guess in the page
        for pat in (
            r'"displayText":"([^"]{4,80})"',
            r"best guess for this image[:\s]+([^<\n]{4,80})",
        ):
            m = re.search(pat, r.text, re.I)
            if m:
                guesses.insert(0, re.sub(r"\\u0027", "'", m.group(1)))
        return {"ok": True, "pages": pages, "guesses": guesses}
    except Exception as e:
        return {"ok": False, "error": str(e)[:180]}


def _parse_html(html: str, engine: str) -> tuple[list, list]:
    soup = BeautifulSoup(html or "", "html.parser")
    guesses = []
    for sel in (
        soup.select_one("meta[property='og:title']"),
        soup.select_one("meta[name='description']"),
    ):
        if sel and sel.get("content"):
            guesses.append(sel["content"][:160])
    text = soup.get_text("\n", strip=True)
    for line in text.splitlines():
        low = line.lower()
        if "appears to contain" in low or "best guess" in low or "this image" in low:
            guesses.append(line[:160])
        if line.startswith("图中可能是"):
            guesses.append(line[:160])
    pages, seen = [], set()
    for a in soup.select("a[href^='http']"):
        href = a.get("href") or ""
        title = " ".join((a.get_text() or a.get("title") or "").split())[:140]
        if not _useful_link(href, title):
            continue
        if href in seen:
            continue
        seen.add(href)
        pages.append({"title": title or _host(href), "url": href, "site": _host(href), "engine": engine})
        if len(pages) >= 12:
            break
    # JSON blobs with titles
    for m in re.finditer(r"https?://(?![^\"']*(?:yandex|bing\.com|microsoft))[^\"'\s]{12,180}", html or ""):
        href = m.group(0).rstrip("\\").rstrip(")")
        if _useful_link(href, "") and href not in seen and "yastatic" not in href:
            seen.add(href)
            pages.append({"title": _host(href), "url": href, "site": _host(href), "engine": engine})
            if len(pages) >= 16:
                break
    return pages, guesses


async def _playwright_yandex(jpeg: bytes) -> dict:
    """Headless Chromium upload to Yandex Images. Times out rather than hanging the 30s budget."""
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
    tmp.write_bytes(jpeg)
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": f"playwright missing: {e}"}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = await browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
            page = await ctx.new_page()
            page.set_default_timeout(11000)
            await page.goto("https://yandex.com/images/", wait_until="domcontentloaded")
            file_inputs = page.locator("input[type=file]")
            if await file_inputs.count() == 0:
                for sel in (
                    "button[aria-label*='Search by image' i]",
                    "button[aria-label*='image' i]",
                    ".input__cbir-button",
                    "[class*='cbir']",
                ):
                    loc = page.locator(sel)
                    if await loc.count():
                        await loc.first.click(timeout=3000)
                        break
            await page.wait_for_timeout(400)
            if await file_inputs.count():
                await file_inputs.first.set_input_files(str(tmp))
            else:
                await browser.close()
                return {"ok": False, "error": "no file input on Yandex Images"}
            try:
                await page.wait_for_url(re.compile(r"rpt=imageview|cbir"), timeout=9000)
            except Exception:
                await page.wait_for_timeout(3500)
            html = await page.content()
            body = ""
            try:
                body = await page.inner_text("body")
            except Exception:
                pass
            pages, guesses = _parse_html(html, "yandex-browser")
            for line in (body or "").splitlines():
                low = line.lower()
                if "appears to contain" in low or line.startswith("图中可能是") or "may be" in low:
                    guesses.append(line[:160])
            await browser.close()
            return {"ok": True, "pages": pages, "guesses": guesses}
    except Exception as e:
        return {"ok": False, "error": str(e)[:180]}
    finally:
        tmp.unlink(missing_ok=True)
