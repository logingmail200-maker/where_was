"""Build region or merged-global indexes for licensed reference imagery."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze.retrieval import build_global_index, build_index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city")
    parser.add_argument("--global-index", action="store_true")
    parser.add_argument("--lightweight", action="store_true", help="Skip the optional SigLIP vectors")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.global_index:
        result = build_global_index()
    elif args.city:
        result = build_index(args.city, batch_size=args.batch_size, siglip=not args.lightweight)
    else:
        parser.error("provide --city NAME or --global-index")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
