from __future__ import annotations

import unittest

from analyze.google_places import _cluster_hits, _queries
from analyze.ocr_tools import apply_gemini_signs
from analyze.think import compose


class EngineCoreTests(unittest.TestCase):
    def test_high_confidence_crop_text_becomes_searchable(self):
        ocr = {"text": "", "lines": [], "names": [], "phones": []}
        visual = {
            "textReads": [
                {"text": "Halli Mane", "kind": "business", "confidence": 0.91, "imageIndex": 3},
                {"text": "3rd Cross Road", "kind": "road", "confidence": 0.86, "imageIndex": 1},
            ],
            "searchableNames": [],
        }
        enriched = apply_gemini_signs(ocr, visual)
        self.assertIn("Halli Mane", enriched["vision_businesses_confident"])
        self.assertIn("3rd Cross Road", enriched["vision_names_confident"])

    def test_model_queries_are_preserved_and_combined(self):
        mind = {
            "city": "Bengaluru",
            "brands": [{"label": "Halli Mane"}],
            "search_names": ["Halli Mane", "3rd Cross Road"],
            "phones": [],
            "model_queries": ["Halli Mane Malleshwaram Bangalore"],
        }
        queries = _queries(mind)
        self.assertEqual(queries[0], "Halli Mane Malleshwaram Bangalore")
        self.assertTrue(any("3rd Cross Road" in query for query in queries))

    def test_repeated_query_hits_form_consensus(self):
        base = {
            "source": "google-maps", "name": "Hallimane Restaurant",
            "address": "Sampige Road, 3rd Cross Rd", "lat": 12.995496,
            "lon": 77.5717835, "osm_id": "one", "query_strength": 0.9,
        }
        merged = _cluster_hits([
            {**base, "query": "Halli Mane Malleshwaram"},
            {**base, "query": "Halli Mane 3rd Cross Road"},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["support_count"], 2)
        self.assertEqual(len(merged[0]["matched_queries"]), 2)

    def test_cluster_prefers_the_visible_primary_brand(self):
        shared = {"source": "google-maps", "lat": 12.9346, "lon": 77.6241, "query_strength": 0.9}
        merged = _cluster_hits(
            [
                {**shared, "name": "CANDERE Lifestyle Jewellery", "address": "18th Main Road", "query": "Kalyan Candere", "osm_id": "c"},
                {**shared, "name": "Kalyan Jewellers Koramangala", "address": "Jyoti Nivas College Road", "query": "Kalyan Bengaluru", "osm_id": "k"},
            ],
            ["Kalyan Jewellers", "Candere"],
        )
        self.assertEqual(merged[0]["name"], "Kalyan Jewellers Koramangala")
        self.assertIn("CANDERE Lifestyle Jewellery", merged[0]["nearby_names"])

    def test_visual_business_and_road_produce_name_plan(self):
        ocr = {
            "text": "", "lines": [], "names": [], "phones": [],
            "vision_names_confident": ["Halli Mane", "3rd Cross Road"],
            "vision_businesses_confident": ["Halli Mane"],
            "vision_agreements": [], "coordinates": [], "infra_words": [],
        }
        visual = {
            "regionHints": ["India", "Karnataka", "Bengaluru"],
            "textReads": [], "searchableNames": [], "searchQueries": [],
            "clues": [], "languagesSeen": ["Kannada"],
        }
        mind = compose(ocr, visual, {}, {"kind": "photo"}, {})
        self.assertEqual(mind["city"], "Bengaluru")
        self.assertIn(mind["plan"], {"intersect", "names"})
        self.assertIn("Halli Mane", mind["search_names"])


if __name__ == "__main__":
    unittest.main()
