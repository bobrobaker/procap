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

from .imageutil import changed_fraction_paths
from .model import Keyframe, Segment, SegmentKind
from .vlm import VLM


def _same_state(a: Keyframe, b: Keyframe, revert_frac: float) -> bool:
    """True if b returns to a's visual state (changed-pixel fraction below revert_frac)."""
    return changed_fraction_paths(a.path, b.path) <= revert_frac


def classify(
    keyframes: list[Keyframe],
    revert_frac: float = 0.0025,
    min_dwell_s: float = 0.0,
) -> list[Segment]:
    """Label each keyframe golden/dross, then group runs of like-labeled frames into Segments."""
    n = len(keyframes)
    if n == 0:
        return []

    kinds = [SegmentKind.GOLDEN] * n
    reasons = ["consequential state change"] * n

    # Revert-detection: kf[i] ... kf[j] return to the same state => the excursion i+1..j is a
    # dead end. j itself is the *return* to a state already seen, so it is backtracking, not a
    # new action — mark it dross too (range is inclusive of j).
    for i in range(n):
        for j in range(i + 2, n):
            if _same_state(keyframes[i], keyframes[j], revert_frac):
                for k in range(i + 1, j + 1):
                    kinds[k] = SegmentKind.DROSS
                    reasons[k] = f"reverted to state @ {keyframes[i].t:.1f}s"
                break

    # Dwell: a golden state held shorter than min_dwell_s is likely a flicker, not an action.
    if min_dwell_s > 0:
        for k in range(n):
            held = max(0.0, keyframes[k].t_end - keyframes[k].t)
            if kinds[k] is SegmentKind.GOLDEN and held < min_dwell_s:
                kinds[k] = SegmentKind.DROSS
                reasons[k] = f"held < {min_dwell_s:.1f}s (transient)"

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
                confidence=0.7, judged_by="heuristic",
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
                confidence=0.7, judged_by="heuristic",
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
    # TODO(stage-2 agent): show the model each low-confidence segment's keyframes and ask
    # "is this a consequential action or an abandoned/inconsequential one?"; update kind/
    # confidence/judged_by from the reply. Keep the heuristic result as the prior.
    return segments
