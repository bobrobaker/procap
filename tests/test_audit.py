"""Stage 3b: audit a generated procedure against a deliberately-gappy written doc."""
from pathlib import Path

from procap.golden import classify
from procap.procedure import synthesize
from procap.audit import audit, parse_written_steps
from procap.model import Procedure, ProcedureStep

WRITTEN = Path(__file__).resolve().parents[1] / "corpus" / "synthetic" / "written_procedure.md"

# A written doc that is reordered (valve before pump), gappy (flow setpoint omitted), and
# has an extra step (calibrate) the video never shows — so an offline content audit on a
# procedure with FILLED intents must fire out_of_order + missing_step + extra_in_doc.
REORDERED_DOC = """# Reordered + gappy reference procedure
1. Open the main valve until valve open shows
2. Start the feed pump and confirm pump running
3. Calibrate the sensor array before first use
"""


def _filled_procedure() -> Procedure:
    """A 3-step procedure with manually-filled intents (no VLM) — the precondition that
    flips the audit from count-baseline to offline lexical content matching."""
    steps = [
        ProcedureStep(0, "Start the feed pump", "", [0], 0.0, 2.0, 2.0,
                      intent="start the feed pump and confirm pump running"),
        ProcedureStep(1, "Open the main valve", "", [1], 2.0, 4.0, 2.0,
                      intent="open the main valve until valve open shows"),
        ProcedureStep(2, "Set the flow setpoint", "", [2], 4.0, 6.0, 2.0,
                      intent="set the flow setpoint to 50 ml per minute"),
    ]
    return Procedure(title="t", source_video="v.mp4", steps=steps)


def test_parse_written_steps_finds_numbered_list():
    steps = parse_written_steps(WRITTEN.read_text())
    # The 3 numbered steps; the H1 title is not a step.
    assert len(steps) == 3, steps


def test_audit_flags_the_omitted_step(extracted_run):
    segs = classify(extracted_run.read_keyframes())
    proc = synthesize(segs, source_video="demo.mp4")
    report = audit(proc, WRITTEN)
    # Placeholder titles -> no content to match -> count baseline.
    assert report.method == "count", report.to_dict()
    # The written doc omits the flow-setpoint step -> at least one missing_step finding.
    assert any(f.kind == "missing_step" for f in report.findings), report.to_dict()
    assert 0.0 < report.coverage < 1.0


def test_lexical_audit_fires_offline_on_filled_intents(tmp_path):
    """The content-gated-not-VLM-gated claim, proven in code: with filled intents and no API
    key, the offline lexical audit recovers reorder + gap + extra — capabilities the count
    baseline structurally cannot reach."""
    doc = tmp_path / "reordered.md"
    doc.write_text(REORDERED_DOC)
    report = audit(_filled_procedure(), doc)  # no key in test env -> lexical path
    assert report.method == "lexical", report.to_dict()
    kinds = {f.kind for f in report.findings}
    assert "out_of_order" in kinds, report.to_dict()      # valve documented before pump
    assert "missing_step" in kinds, report.to_dict()      # flow setpoint omitted
    assert "extra_in_doc" in kinds, report.to_dict()      # calibrate not in the video
    # under_documented is a thinness judgement left to the VLM — never emitted offline.
    assert "under_documented" not in kinds, report.to_dict()


def test_placeholder_titles_do_not_trigger_lexical(tmp_path):
    """Safety rail: bare [fill in] titles carry no content, so the audit stays on the count
    baseline rather than fabricating lexical matches from placeholders."""
    doc = tmp_path / "doc.md"
    doc.write_text(REORDERED_DOC)
    steps = [ProcedureStep(i, "[fill in: what are you doing in this step?]", "", [i],
                           float(i), float(i + 1), 1.0) for i in range(3)]
    report = audit(Procedure(title="t", source_video="v.mp4", steps=steps), doc)
    assert report.method == "count", report.to_dict()
