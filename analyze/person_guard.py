"""Privacy guard for place-photo analysis.

The detector runs before fine-grained retrieval. It is lazy because the
torchvision weights are sizeable; when unavailable, normal place analysis may
continue but retrieval remains opt-in and never receives face crops.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _model():
    import torch
    from torchvision.models.detection import FasterRCNN_MobileNet_V3_Large_320_FPN_Weights
    from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_320_fpn

    weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT
    return fasterrcnn_mobilenet_v3_large_320_fpn(weights=weights).eval(), weights.transforms(), torch


def detect_prominent_person(path: Path, *, min_score: float = 0.70, min_area: float = 0.09) -> dict:
    """Detect a person covering a meaningful fraction of the frame.

    A small distant pedestrian does not block place matching. A person taking
    at least 9% of the frame does, avoiding fine-grained location inference
    from personal photos.
    """
    try:
        from PIL import Image

        model, transform, torch = _model()
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            tensor = transform(rgb)
        with torch.inference_mode():
            output = model([tensor])[0]
        largest = 0.0
        for label, score, box in zip(output["labels"], output["scores"], output["boxes"]):
            # COCO label 1 is person.
            if int(label) != 1 or float(score) < min_score:
                continue
            left, top, right, bottom = (float(value) for value in box.tolist())
            largest = max(largest, max(0.0, right - left) * max(0.0, bottom - top) / max(1.0, width * height))
        return {"available": True, "prominent": largest >= min_area, "largest_person_fraction": round(largest, 4)}
    except Exception as exc:
        return {"available": False, "prominent": False, "largest_person_fraction": 0.0, "error": str(exc)[:140]}
