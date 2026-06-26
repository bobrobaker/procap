"""Stage 2 — classify the keyframe timeline into golden vs dross segments.

Baseline heuristic (always on, no VLM):
  - revert-detection: if a later keyframe's perceptual hash *returns* to an earlier
    keyframe's state, everything strictly between them was an excursion the operator
    abandoned -> dross. This is the spec's "clicked into the wrong part and went back".
  - dwell: a state held very briefly that is itself part of a revert is dross.
  - everything else is golden.

When a VLM is available, `refine_with_vlm` re-judges low-confidence segments by showing
the model the segment's keyframes. The heuristic must stand alone (see the decision doc).

This is the baseline spine; tuning the thresholds to hit the roadmap's F1 target on the
synthetic clip and adding the VLM refinement is the stage-2 workstream.
"""
from __future__ import annotations

import math

from .imageutil import changed_fraction_paths
from .model import Keyframe, Segment, SegmentKind
from .vlm import VLM, extract_json

# Each heuristic segment carries a real per-segment confidence derived from the margin of
# its own decision (see _golden_confidence / _revert_confidence / _dwell_confidence). A
# segment below _AMBIGUOUS_BELOW is "ambiguous" and worth a VLM second opinion; a confident
# heuristic call (>= 0.8) correctly skips the VLM. A VLM verdict is only allowed to overwrite
# the heuristic prior when the model is at least _VLM_MIN_CONFIDENCE sure, so a hedged reply
# leaves the baseline untouched. Confidences are normalised to roughly _CONF_FLOOR.._CONF_CEIL
# so genuinely low-margin calls land below _AMBIGUOUS_BELOW and stay eligible for refinement.
_AMBIGUOUS_BELOW = 0.8
_VLM_MIN_CONFIDENCE = 0.6
_CONF_FLOOR = 0.5
_CONF_CEIL = 0.95


def _golden_confidence(change_score: float, change_threshold: float,
                       click_detected: bool) -> float:
    """Confidence that a kept keyframe is a consequential action.

    From the margin of durable change over the extract threshold: a change far above the
    threshold is a big, clean state change (high confidence); a change just over it is a
    marginal call (low confidence). Log-scaled so the first frame's change_score=1.0
    saturates near the ceiling while marginal changes stay below _AMBIGUOUS_BELOW and remain
    eligible for VLM refinement. A detected click adds a small boost (a deliberate action).
    """
    margin = max(1.0, change_score / change_threshold) if change_threshold > 0 else 1.0
    span = min(1.0, math.log10(margin) / 2.0)
    conf = _CONF_FLOOR + (_CONF_CEIL - _CONF_FLOOR) * span
    if click_detected:
        conf = min(_CONF_CEIL, conf + 0.05)
    return round(conf, 3)


def _revert_confidence(changed_frac: float, revert_frac: float) -> float:
    """Confidence that a stretch is dross because it returned to an earlier state.

    From how cleanly it returned: a changed fraction far below the revert threshold is a
    pixel-near-identical backtrack (high confidence); one brushing the threshold is a
    borderline match (low confidence)."""
    ratio = min(1.0, changed_frac / revert_frac) if revert_frac > 0 else 0.0
    return round(_CONF_FLOOR + (_CONF_CEIL - _CONF_FLOOR) * (1.0 - ratio), 3)


def _dwell_confidence(held: float, min_dwell_s: float) -> float:
    """Confidence that a stretch is dross because it was held too briefly to be an action.

    From how far below min_dwell_s the hold was: a near-zero hold is clearly a transient
    flicker (high confidence); a hold just under the floor is a marginal call (low)."""
    ratio = min(1.0, held / min_dwell_s) if min_dwell_s > 0 else 0.0
    return round(_CONF_FLOOR + (_CONF_CEIL - _CONF_FLOOR) * (1.0 - ratio), 3)


def _mean_conf(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else _CONF_FLOOR

_REFINE_PROMPT = (
    "These are consecutive keyframes from a screen recording of someone operating a "
    "technical GUI, in time order. Decide whether this stretch is a CONSEQUENTIAL action "
    "(a real, kept change to the system — keep it) or an ABANDONED/INCONSEQUENTIAL one "
    "(a mis-click that was reverted, mouse wander, idle dwell — drop it).\n"
    'Reply with ONLY a JSON object: {"kind": "golden" | "dross", '
    '"confidence": <0..1>, "reason": "<short phrase>"}. '
    "Use \"golden\" for consequential, \"dross\" for abandoned/inconsequential."
)


def classify(
    keyframes: list[Keyframe],
    revert_frac: float = 0.0025,
    min_dwell_s: float = 0.0,
    change_threshold: float = 0.0025,
) -> list[Segment]:
    """Label each keyframe golden/dross, then group runs of like-labeled frames into Segments.

    `change_threshold` is the extract stage's keyframe threshold; golden confidence is the
    margin of a keyframe's change_score over it.
    """
    n = len(keyframes)
    if n == 0:
        return []

    kinds = [SegmentKind.GOLDEN] * n
    reasons = ["consequential state change"] * n
    # Per-keyframe confidence, computed from each decision's own margin (see helpers). Golden
    # is the default prior; revert/dwell overwrite it when those decisions fire.
    confs = [
        _golden_confidence(kf.change_score, change_threshold, kf.click_detected)
        for kf in keyframes
    ]

    # Revert-detection: kf[i] ... kf[j] return to the same state => the excursion i+1..j is a
    # dead end. j itself is the *return* to a state already seen, so it is backtracking, not a
    # new action — mark it dross too (range is inclusive of j).
    for i in range(n):
        for j in range(i + 2, n):
            changed = changed_fraction_paths(keyframes[i].path, keyframes[j].path)
            if changed <= revert_frac:
                conf = _revert_confidence(changed, revert_frac)
                for k in range(i + 1, j + 1):
                    kinds[k] = SegmentKind.DROSS
                    reasons[k] = f"reverted to state @ {keyframes[i].t:.1f}s"
                    confs[k] = conf
                break

    # Dwell: a golden state held shorter than min_dwell_s is likely a flicker, not an action.
    if min_dwell_s > 0:
        for k in range(n):
            held = max(0.0, keyframes[k].t_end - keyframes[k].t)
            if kinds[k] is SegmentKind.GOLDEN and held < min_dwell_s:
                kinds[k] = SegmentKind.DROSS
                reasons[k] = f"held < {min_dwell_s:.1f}s (transient)"
                confs[k] = _dwell_confidence(held, min_dwell_s)

    # Segment grouping: each GOLDEN keyframe is its own segment (one consequential action ->
    # one procedure step). Consecutive DROSS keyframes collapse into a single dross segment.
    segments: list[Segment] = []
    k = 0
    while k < n:
        if kinds[k] is SegmentKind.GOLDEN:
            kf = keyframes[k]
            segments.append(Segment(
                start_t=kf.t, end_t=kf.t_end, keyframe_indexes=[kf.index],
                kind=SegmentKind.GOLDEN.value, reason=reasons[k],
                confidence=confs[k], judged_by="heuristic",
            ))
            k += 1
        else:
            start = k
            while k < n and kinds[k] is SegmentKind.DROSS:
                k += 1
            block = keyframes[start:k]
            segments.append(Segment(
                start_t=block[0].t, end_t=block[-1].t_end,
                keyframe_indexes=[kf.index for kf in block],
                kind=SegmentKind.DROSS.value, reason=reasons[start],
                confidence=_mean_conf(confs[start:k]), judged_by="heuristic",
            ))
    return segments


def refine_with_vlm(segments: list[Segment], keyframes: list[Keyframe],
                    vlm: VLM | None = None) -> list[Segment]:
    """Re-judge ambiguous segments with the VLM when available; pass-through otherwise.

    Stage-2 workstream: implement the prompt + parsing. Until then this is a guarded no-op
    so the pipeline runs unchanged with no key.
    """
    vlm = vlm or VLM()
    if not vlm.available:
        return segments

    by_index = {kf.index: kf for kf in keyframes}
    for seg in segments:
        if seg.confidence >= _AMBIGUOUS_BELOW:
            continue  # heuristic is already confident; don't spend a VLM call
        paths = [by_index[i].path for i in seg.keyframe_indexes if i in by_index]
        if not paths:
            continue  # nothing to show the model; keep the prior

        try:
            reply = vlm.ask(_REFINE_PROMPT, image_paths=paths)
            data = extract_json(reply)
            kind = str(data["kind"]).strip().lower()
            conf = float(data.get("confidence", 0.0))
        except Exception:
            continue  # parse/IO error -> keep the heuristic prior, never crash the stage

        if kind not in (SegmentKind.GOLDEN.value, SegmentKind.DROSS.value):
            continue
        if conf < _VLM_MIN_CONFIDENCE:
            continue  # hedged verdict: leave the heuristic prior in place

        # Confident verdict: adopt it (this is the only path that flips a label).
        seg.kind = kind
        seg.confidence = conf
        seg.judged_by = "vlm"
        reason = data.get("reason")
        if reason:
            seg.reason = str(reason)
    return segments
