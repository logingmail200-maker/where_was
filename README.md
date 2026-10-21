# WhereWas

Local website: drop a photo or screenshot, get a **pin or a shortlist with a radius** — not a fake exact address.

## What this build does (~30s)

1. Photo vs screenshot
2. EXIF GPS (still reads the frame)
3. OCR on the full frame **and edge crops** — names, India plates, plus codes, Maps `@lat,lon` URLs
4. Reverse image search: Yandex + Bing, then headless Chromium if HTML is junk
5. OpenStreetMap names + **two-clue pairs** (two different names within 800 m) + flyover/metro if the photo mentions them
6. Rank: unique + second clue → pin; chain/plate → region
7. Street View **on the shortlist** (not a scan of the planet)

Kannada/Hindi OCR needs extra language packs (not installed in this environment). Visual “architecture guess” needs a Gemini key in `GEMINI_API_KEY` (not required).

## Run

```bash
cd wherewas
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Open the printed URL. Do not use `file://`.

Nominatim requires light use (this app waits ~1s between searches). Reverse-search engines may CAPTCHA datacenter IPs.
