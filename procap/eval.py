"""Score golden/dross segmentation against ground-truth label stretches.

Used by the synthetic-corpus tests (and the stage-2 workstream) to measure how well
`golden.classify` recovers the scripted golden actions. We compare on a fine time grid:
each sample point is golden-or-dross in both the prediction and the ground truth, then we
compute precision/recall/F1 for the GOLDEN class (the positive class we care about).
"""
from __future__ import annotations

from .model import Segment, SegmentKind


def _kind_at(spans: list[tuple[float, float, str]], t: float) -> str | None:
    for start, end, kind in spans:
        if start <= t < end:
            return kind
    return None


def score_against_labels(
    segments: list[Segment],
    labels: list[dict],
    step: float = 0.1,
) -> dict:
    """Return precision/recall/f1 for golden detection on a `step`-second time grid.

    `labels` is the corpus ground truth: [{"start", "end", "kind"}], kind in golden/dross.
    """
    pred = [(s.start_t, s.end_t, s.kind) for s in segments]
    truth = [(l["start"], l["end"], l["kind"]) for l in labels]
    if not truth:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "n": 0}

    end = max(e for _s, e, _k in truth)
    tp = fp = fn = tn = 0
    t = 0.0
    g = SegmentKind.GOLDEN.value
    while t < end:
        p = _kind_at(pred, t)
        y = _kind_at(truth, t)
        if y is not None:
            if p == g and y == g:
                tp += 1
            elif p == g and y != g:
                fp += 1
            elif p != g and y == g:
                fn += 1
            else:
                tn += 1
        t += step

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn, "tn": tn}
