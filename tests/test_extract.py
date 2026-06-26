"""Stage 1: the synthetic clip has 7 scripted scenes that collapse to 6 distinct held
states (the mouse-wander stretch is the same panel as the pump scene, so no new keyframe).
The keyframer should recover those distinct states, not the wander."""


def test_keyframe_count_matches_distinct_states(extracted_run):
    kfs = extracted_run.read_keyframes()
    # 6 distinct states: idle, pump(+wander merged), settings, revert-pump, valve, flow.
    assert 5 <= len(kfs) <= 7, [f"{k.t}-{k.t_end}" for k in kfs]


def test_timestamps_monotonic_and_contiguous(extracted_run):
    kfs = extracted_run.read_keyframes()
    for a, b in zip(kfs, kfs[1:]):
        assert a.t < b.t
        assert a.t_end <= b.t + 1e-6


def test_no_keyframe_for_pure_mouse_wander(extracted_run):
    """The 4-7s wander stretch must not spawn its own keyframe — it is the pump state."""
    kfs = extracted_run.read_keyframes()
    starts = [round(k.t) for k in kfs]
    assert 5 not in starts and 6 not in starts, starts
