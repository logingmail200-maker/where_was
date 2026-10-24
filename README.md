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

## Evaluation baseline (Stage 1)

Before changing the search engine, measure it with a labelled set of place
photos. Create a CSV such as:

```csv
image_path,true_lat,true_lon
eval_images/example-1.jpg,12.9346427,77.6241613
eval_images/example-2.jpg,-37.819909,144.983225
```

Then run the same pipeline used by the API and save a reproducible report:

```bash
python tools/evaluate_pipeline.py eval.csv --output reports/baseline.json
```

The report includes top-1 accuracy and top-10 recall within 1 km, 25 km and
200 km, the median top-1 error, and every candidate/error pair. It runs one
image at a time to respect public geocoder limits. Use a dedicated test set
whose locations were not used to tune the system, and keep EXIF-bearing and
EXIF-stripped photos in separate reports so GPS does not inflate results.

Potential issues: provider rate limits/CAPTCHAs may produce failed rows; image
paths are resolved relative to the CSV; and an unbalanced set (for example,
mostly famous landmarks) will overstate real-world performance. Inspect failed
rows and per-image errors in the JSON report before comparing changes.

## Licensed street-image retrieval

WhereWas searches only a local index made from imagery you are licensed to
use. It never bulk-downloads or indexes Google Street View. Configure a
Mapillary token in the gitignored `.env`, then start with a small Bengaluru
area before collecting a larger city dataset:

```bash
python tools/download_mapillary_reference.py --city bengaluru --bbox 12.90,77.54,12.99,77.65 --max-images 1000
python tools/build_reference_index.py --city bengaluru
```

The collector records coordinates, provider, license, and required Mapillary
attribution in `metadata.jsonl`. The app uses the index only after routing an
image to a city; it does not perform a live worldwide street-image search.

CPU indexing can take minutes for thousands of images and visual verification
is slower on CPU. A CUDA GPU materially speeds both embedding creation and
verification. In production, keep the reference data outside Git and mount it
as persistent storage.
