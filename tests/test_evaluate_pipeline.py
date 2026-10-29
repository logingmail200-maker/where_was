from pathlib import Path

from tools.evaluate_pipeline import EvaluationItem, candidate_distances, haversine_m, summarize


def test_haversine_known_distance() -> None:
    # A degree of longitude at the equator is roughly 111.195 km.
    assert 111_000 < haversine_m(0, 0, 0, 1) < 112_000


def test_summary_counts_missing_candidates_as_misses() -> None:
    records = [
        {"status": "ok", "top1_error_m": 500.0, "candidates": [{"error_m": 500.0}]},
        {"status": "failed", "candidates": []},
    ]
    summary = summarize(records)
    assert summary["thresholds_km"]["1"]["top1_accuracy_percent"] == 50.0
    assert summary["thresholds_km"]["1"]["top10_recall_percent"] == 50.0


def test_candidate_distances_preserves_rank() -> None:
    item = EvaluationItem(Path("scene.jpg"), 0.0, 0.0, {})
    distances = candidate_distances({"candidates": [{"lat": 0, "lon": 0, "name": "correct"}]}, item, 10)
    assert distances[0]["rank"] == 1
    assert distances[0]["error_m"] == 0.0
