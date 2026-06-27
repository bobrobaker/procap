"""Stage 3a: procedure synthesis from golden segments (offline/heuristic path)."""
from procap.golden import classify
from procap.procedure import synthesize, render_markdown, DEFAULT_MAX_ACTIVE_S


def test_one_step_per_golden_segment(extracted_run):
    segs = classify(extracted_run.read_keyframes())
    proc = synthesize(segs, source_video="demo.mp4")
    n_golden = sum(1 for s in segs if s.kind == "golden")
    assert len(proc.steps) == n_golden
    assert n_golden >= 3  # idle, pump, valve, flow (revert/excursion excluded)


def test_durations_real_and_total_consistent(extracted_run):
    segs = classify(extracted_run.read_keyframes())
    proc = synthesize(segs, source_video="demo.mp4")
    assert all(s.est_seconds > 0 for s in proc.steps)
    assert abs(proc.total_est_seconds - sum(s.est_seconds for s in proc.steps)) < 0.05


def test_offline_titles_are_fillin_placeholders(extracted_run):
    segs = classify(extracted_run.read_keyframes())
    proc = synthesize(segs, source_video="demo.mp4")  # no API key in test env
    assert all("fill in" in s.title for s in proc.steps)
    md = render_markdown(proc)
    assert "Procedure" in md and "est" in md


def test_held_accounting_is_recall_safe_decomposition(extracted_run):
    """held_seconds decomposes a long dwell honestly without ever dropping a step:
    active + held == est, held >= 0, and held > 0 only past the active cap."""
    segs = classify(extracted_run.read_keyframes())
    n_golden = sum(1 for s in segs if s.kind == "golden")
    proc = synthesize(segs, source_video="demo.mp4", max_active_s=DEFAULT_MAX_ACTIVE_S)
    assert len(proc.steps) == n_golden  # recall-safe: no step dropped by the accounting
    for s in proc.steps:
        active = s.est_seconds - s.held_seconds
        assert s.held_seconds >= 0.0
        assert active >= 0.0
        assert abs(active + s.held_seconds - s.est_seconds) < 1e-9
        if s.est_seconds <= DEFAULT_MAX_ACTIVE_S:
            assert s.held_seconds == 0.0
        else:
            assert s.held_seconds > 0.0
    # the synthetic clip's temp-stabilize hold (5s, past the 3s active cap) must surface held time
    assert any(s.held_seconds > 0 for s in proc.steps)
    md = render_markdown(proc)
    assert "held" in md  # the honest decomposition reaches the rendered procedure


def test_high_active_cap_yields_no_held(extracted_run):
    segs = classify(extracted_run.read_keyframes())
    proc = synthesize(segs, source_video="demo.mp4", max_active_s=1000.0)
    assert all(s.held_seconds == 0.0 for s in proc.steps)
