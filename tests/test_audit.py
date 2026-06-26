"""Stage 3b: audit a generated procedure against a deliberately-gappy written doc."""
from pathlib import Path

from procap.golden import classify
from procap.procedure import synthesize
from procap.audit import audit, parse_written_steps

WRITTEN = Path(__file__).resolve().parents[1] / "corpus" / "synthetic" / "written_procedure.md"


def test_parse_written_steps_finds_numbered_list():
    steps = parse_written_steps(WRITTEN.read_text())
    # The 3 numbered steps; the H1 title is not a step.
    assert len(steps) == 3, steps


def test_audit_flags_the_omitted_step(extracted_run):
    segs = classify(extracted_run.read_keyframes())
    proc = synthesize(segs, source_video="demo.mp4")
    report = audit(proc, WRITTEN)
    # The written doc omits the flow-setpoint step -> at least one missing_step finding.
    assert any(f.kind == "missing_step" for f in report.findings), report.to_dict()
    assert 0.0 < report.coverage < 1.0
