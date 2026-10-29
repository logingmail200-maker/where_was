from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from analyze import retrieval


def _reference(root: Path, region: str, name: str, color: tuple[int, int, int], lat: float) -> None:
    folder = root / region
    images = folder / "images"
    images.mkdir(parents=True)
    path = images / f"{name}.jpg"
    Image.new("RGB", (160, 100), color).save(path)
    row = {"id": name, "provider": "mapillary", "local_path": f"images/{name}.jpg", "lat": lat, "lon": 77.0}
    with (folder / "metadata.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")


class RetrievalTests(unittest.TestCase):
    def test_build_global_and_route_multiple_regions(self):
        previous = os.environ.get("WHEREWAS_RETRIEVAL_ROOT")
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                os.environ["WHEREWAS_RETRIEVAL_ROOT"] = directory
                retrieval._load_shard.cache_clear()
                _reference(root, "bengaluru", "green", (30, 130, 40), 12.9)
                _reference(root, "dubai", "sand", (190, 170, 110), 25.2)
                retrieval.build_index("bengaluru", siglip=False)
                retrieval.build_index("dubai", siglip=False)
                result = retrieval.build_global_index()
                self.assertEqual(result["images"], 2)
                self.assertEqual(retrieval.route_regions({"city": "Bangalore"}), ["bengaluru"])
                self.assertEqual(retrieval.route_regions({"regional_hypotheses": [{"country": "UAE"}]}), ["dubai"])
                query = root / "query.jpg"
                Image.new("RGB", (160, 100), (32, 128, 38)).save(query)
                hits = retrieval.search(query, mind={"city": "Bangalore"}, top_k=2)
                self.assertTrue(hits)
                self.assertEqual(hits[0]["region_shard"], "bengaluru")
                self.assertTrue(np.isfinite(hits[0]["similarity"]))
                # Windows cannot delete memory-mapped arrays while the cache
                # owns them; production keeps these maps for process lifetime.
                retrieval._load_shard.cache_clear()
        finally:
            if previous is None:
                os.environ.pop("WHEREWAS_RETRIEVAL_ROOT", None)
            else:
                os.environ["WHEREWAS_RETRIEVAL_ROOT"] = previous
            retrieval._load_shard.cache_clear()


if __name__ == "__main__":
    unittest.main()
