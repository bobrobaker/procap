"""Stage 3a: procedure synthesis from golden segments (offline/heuristic path)."""
from procap.golden import classify
from procap.procedure import synthesize, render_markdown


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
