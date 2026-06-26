"""Stage 2: the heuristic classifier should recover the scripted golden actions and reject
the dross (mouse wander, wrong-tab excursion, revert) on the labeled synthetic clip."""
from procap.golden import classify
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
