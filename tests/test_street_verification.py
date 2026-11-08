import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np

from analyze.street_sources import _orb_check, verify_shortlist_on_demand


class StreetVerificationTests(unittest.TestCase):
    def test_blank_image_cannot_verify(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'blank.png'
            blank = np.zeros((256, 256), dtype=np.uint8)
            cv2.imwrite(str(path), blank)
            _, encoded = cv2.imencode('.png', blank)
            self.assertEqual(_orb_check(path, encoded.tobytes()), (0, 0.0, 0.0, 0))

    def test_matching_features_must_span_the_frame(self):
        rng = np.random.default_rng(42)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scene.png'
            frame = rng.integers(0, 256, (480, 640), dtype=np.uint8)
            cv2.imwrite(str(path), frame)
            _, encoded = cv2.imencode('.png', frame)
            inliers, ratio, coverage, cells = _orb_check(path, encoded.tobytes())
            self.assertGreater(inliers, 12)
            self.assertGreater(ratio, .28)
            self.assertGreater(coverage, .03)
            self.assertGreaterEqual(cells, 3)


class NoCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_coverage_does_not_boost_score(self):
        candidate = {'lat': 12.9, 'lon': 77.6, 'score': 50, 'radius_m': 1000}
        with patch('analyze.street_sources.mapillary_nearby', new=AsyncMock(return_value=[])):
            await verify_shortlist_on_demand(Path('unused.jpg'), [candidate])
        self.assertEqual(candidate['score'], 50)
        self.assertEqual(candidate['radius_m'], 1000)
        self.assertNotIn('reference_image', candidate)
