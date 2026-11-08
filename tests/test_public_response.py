import json
import unittest

from analyze.pipeline import _event, _public_result


class PublicResponseTests(unittest.TestCase):
    def test_result_excludes_internal_search_trace(self):
        candidate = {
            "id": "retrieval-mapillary-secret-reference", "name": "Possible place",
            "lat": 12.9, "lon": 77.6, "score": 68, "radius_m": 800,
            "tier": "city", "why": ["detected private clue"],
            "source": "retrieval", "base_score": 88,
        }
        data = _public_result({
            "analysis_id": "sample", "candidates": [candidate], "primary": candidate,
            "visual": {"searchQueries": ["private query"]},
            "ocr_text": "private text", "reference_coverage": {"installed_regions": ["private region"]},
        })
        body = json.dumps(data)
        self.assertEqual(data["candidates"][0]["id"], data["primary"]["id"])
        self.assertEqual(data["candidates"][0]["radius_m"], 800)
        for secret in ("retrieval-mapillary", "detected private clue", "private query", "private text", "private region"):
            self.assertNotIn(secret, body)

    def test_progress_excludes_detail_and_search_branch(self):
        step = json.loads(_event({"type": "step", "id": "ocr", "status": "done",
                                  "label": "Reading visible text", "detail": "private sign"}))
        self.assertEqual(step, {"type": "step", "id": "search", "status": "done", "label": "Searching places"})


if __name__ == "__main__":
    unittest.main()
