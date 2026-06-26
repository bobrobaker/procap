"""Build runs/audit_demo — a demonstrating fixture for the OFFLINE lexical content audit.

The main demo run (labeled_demo) has `[fill in]` placeholder titles, so its audit can only
do the count baseline. This fixture is a procedure with **manually filled intents** (no VLM)
audited against a written doc that is reordered + gappy + has an extra step
(`written_procedure_reordered.md`). Run offline it fires out_of_order + missing_step +
extra_in_doc — demonstrating in a live run what `tests/test_audit.py` asserts: content audit
is *content-gated, not VLM-gated*. Content here comes from a human fill-in, no model.

Reproducible: depends only on the committed `labeled_demo` keyframes (regenerate them with
`procap run corpus/synthetic/labeled_demo.mp4`) plus this script.

    .venv/bin/python corpus/make_audit_demo.py
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from procap.run import Run
from procap.model import Procedure, ProcedureStep
from procap import audit as audit_stage, procedure as procedure_stage

SRC = Path("runs/labeled_demo")
DST = Path("runs/audit_demo")
DOC = Path("corpus/synthetic/written_procedure_reordered.md")

# Filled intents (a human's "what am I doing here"), referencing keyframes that exist in the
# labeled_demo run. The titles/intents are deliberate so the lexical match is unambiguous.
STEPS = [
    (1, "Start the feed pump", "start the feed pump and confirm pump running"),
    (4, "Open the main valve", "open the main valve until valve open shows"),
    (5, "Set the flow setpoint", "set the flow setpoint to 50 ml per minute"),
]


def main() -> int:
    if not (SRC / "keyframes.json").exists():
        raise SystemExit(
            f"{SRC}/keyframes.json missing — run `procap run corpus/synthetic/labeled_demo.mp4` first"
        )
    run = Run(DST).ensure()
    # Reuse the labeled_demo keyframe images + the keyframe/segment artifacts so the demo
    # renders the earlier stages too.
    for name in ("keyframes.json", "segments.json"):
        shutil.copyfile(SRC / name, DST / name)
    if (SRC / "keyframes").exists():
        shutil.copytree(SRC / "keyframes", DST / "keyframes", dirs_exist_ok=True)

    kf_by_index = {k.index: k for k in run.read_keyframes()}
    steps = []
    for i, (kf_idx, title, intent) in enumerate(STEPS):
        if kf_idx not in kf_by_index:
            raise SystemExit(f"keyframe #{kf_idx} not in {SRC} — adjust STEPS")
        kf = kf_by_index[kf_idx]
        steps.append(ProcedureStep(
            index=i, title=title, description="", keyframe_indexes=[kf_idx],
            start_t=kf.t, end_t=kf.t_end, est_seconds=round(kf.t_end - kf.t, 1),
            intent=intent,
            held_seconds=round(max(0.0, (kf.t_end - kf.t) - procedure_stage.DEFAULT_MAX_ACTIVE_S), 1),
        ))
    proc = Procedure(title="Procedure from audit_demo (filled intents)",
                     source_video="corpus/synthetic/audit_demo.mp4",
                     steps=steps,
                     total_est_seconds=round(sum(s.est_seconds for s in steps), 1))
    run.write_procedure(proc)
    (DST / "procedure.md").write_text(procedure_stage.render_markdown(proc))

    report = audit_stage.audit(proc, DOC)  # offline (no key) + filled intents -> lexical
    run.write_audit(report)
    (DST / "audit.md").write_text(audit_stage.render_markdown(report))

    run.write_meta(source_video="corpus/synthetic/audit_demo.mp4", fps_sampled=2.0,
                   duration=15.0, n_keyframes=len(kf_by_index),
                   max_active_s=procedure_stage.DEFAULT_MAX_ACTIVE_S,
                   match_floor=audit_stage.DEFAULT_MATCH_FLOOR,
                   note="demonstrating fixture for the offline lexical content audit")

    kinds = sorted({f.kind for f in report.findings})
    print(f"built {DST} — audit method={report.method}, findings={kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
