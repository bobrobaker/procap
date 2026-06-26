"""Stage 2: the heuristic classifier should recover the scripted golden actions and reject
the dross (mouse wander, wrong-tab excursion, revert) on the labeled synthetic clip."""
from procap.golden import (
    classify,
    _golden_confidence,
    _revert_confidence,
    _dwell_confidence,
)
from procap.eval import score_against_labels


def test_golden_f1_meets_target(extracted_run, synthetic_labels):
    segs = classify(extracted_run.read_keyframes())
    score = score_against_labels(segs, synthetic_labels)
    assert score["f1"] >= 0.8, score


def test_excursion_marked_dross(extracted_run):
    segs = classify(extracted_run.read_keyframes())
    # The settings/revert stretch around 7-11s must be dross.
    dross = [s for s in segs if s.kind == "dross"]
    assert any(s.start_t <= 8.0 <= s.end_t for s in dross), [(s.kind, s.start_t, s.end_t) for s in segs]


def test_one_golden_segment_per_action(extracted_run):
    segs = classify(extracted_run.read_keyframes())
    for s in segs:
        if s.kind == "golden":
            assert len(s.keyframe_indexes) == 1  # one consequential action per golden segment


def test_confidence_is_real_and_varies(extracted_run):
    """Confidence is a real per-segment number in (0,1] (not the old constant 0.7), and it
    actually varies across segments — different decisions have different margins."""
    segs = classify(extracted_run.read_keyframes())
    confs = [s.confidence for s in segs]
    assert all(0.0 < c <= 1.0 for c in confs), confs
    assert len(set(confs)) > 1, confs              # not a constant
    assert all(c != 0.7 for c in confs), confs     # not the old hardcoded value


def test_clean_revert_scores_higher_than_marginal():
    """A near-pixel-identical return is a more certain backtrack than one that barely meets
    the revert threshold."""
    revert_frac = 0.0025
    clean = _revert_confidence(0.0001, revert_frac)      # almost identical
    marginal = _revert_confidence(0.0024, revert_frac)   # brushing the threshold
    assert clean > marginal
    assert 0.0 < marginal < clean <= 1.0


def test_golden_confidence_grows_with_margin():
    """A big, clean change is more certainly consequential than a marginal one, and a marginal
    change stays below the VLM-refinement gate (0.8)."""
    big = _golden_confidence(1.0, 0.0025, click_detected=False)
    marginal = _golden_confidence(0.005, 0.0025, click_detected=False)
    assert big > marginal
    assert marginal < 0.8           # genuinely low-margin -> still eligible for VLM refinement
    # A detected click nudges confidence up but does not flip a marginal call past the gate.
    assert _golden_confidence(0.005, 0.0025, click_detected=True) > marginal


def test_dwell_confidence_grows_as_hold_shrinks():
    """The briefer the hold below the dwell floor, the more certainly it is a transient."""
    flicker = _dwell_confidence(0.05, 0.6)   # held almost no time
    borderline = _dwell_confidence(0.55, 0.6)  # nearly met the floor
    assert flicker > borderline
    assert 0.0 < borderline < flicker <= 1.0
