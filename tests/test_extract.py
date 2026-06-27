"""Stage 1: the synthetic clip has 10 scripted scenes that collapse to 9 distinct held
states (the mouse-wander stretch is the same panel as the revert-valve scene, so no new
keyframe). The keyframer should recover those distinct states, not the wander."""


def test_keyframe_count_matches_distinct_states(extracted_run):
    kfs = extracted_run.read_keyframes()
    # 9 distinct states: idle, pump, valve, settings, revert-valve(+wander merged),
    # flow, heater, temp, logging.
    assert 8 <= len(kfs) <= 10, [f"{k.t}-{k.t_end}" for k in kfs]


def test_timestamps_monotonic_and_contiguous(extracted_run):
    kfs = extracted_run.read_keyframes()
    for a, b in zip(kfs, kfs[1:]):
        assert a.t < b.t
        assert a.t_end <= b.t + 1e-6


def test_no_keyframe_for_pure_mouse_wander(extracted_run):
    """The 10-13s wander stretch must not spawn its own keyframe — it is the valve state."""
    kfs = extracted_run.read_keyframes()
    starts = [round(k.t) for k in kfs]
    assert 11 not in starts and 12 not in starts, starts
