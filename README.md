# WhereWas

Local website: drop a photo or screenshot, get a **pin or a shortlist with a radius** — not a fake exact address.

## What this build does (time-budgeted for ~20–25s)

1. Photo vs screenshot
2. EXIF GPS (still reads the frame)
3. In parallel: OCR, multimodal scene clues, reverse search and metadata lookup
4. Route clues to several licensed regional indexes while also searching the merged global index
5. Geometrically verify likely frames with SIFT/homography; colour/terrain similarity alone cannot create a street pin
6. OpenStreetMap/Places names + **two-clue pairs** + nearby Mapillary verification
7. Fuse the evidence into a top-10 shortlist with confidence/radius, then open Street View only for inspection

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
python tools/build_reference_index.py --city bengaluru --lightweight
python tools/build_reference_index.py --global-index
```

The collector records coordinates, provider, license, and required Mapillary
attribution in `metadata.jsonl`. The app searches the global index and up to
five clue-routed shards concurrently. The global index stores vectors and
metadata only, so it does not duplicate all downloaded images.

The default Render configuration uses a compact OpenCV descriptor because a
free 512 MB service cannot reliably load SigLIP. It is a coarse prefilter and
requires geometric agreement before a fine result is allowed. For materially
better worldwide retrieval, build SigLIP vectors on a GPU/memory-equipped
machine with:

```bash
pip install -r requirements-retrieval.txt
python tools/build_reference_index.py --city bengaluru
python tools/build_reference_index.py --global-index
```

No algorithm can match an arbitrary world street if licensed reference
imagery does not cover that place. `reference_coverage` in every API result
makes this limitation explicit instead of returning a fabricated exact pin.

CPU indexing can take minutes for thousands of images and visual verification
is slower on CPU. A CUDA GPU materially speeds both embedding creation and
verification. In production, keep the reference data outside Git and mount it
as persistent storage.
