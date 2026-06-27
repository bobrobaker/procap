"""procap CLI — `procap <stage> ...`. Each stage reads/writes the run dir (procap.run).

    procap extract VIDEO [--fps] [--run DIR]      video -> keyframes.json
    procap golden  RUN                            keyframes.json -> segments.json
    procap procedure RUN                          segments.json -> procedure.json/.md
    procap audit   RUN --against DOC              procedure.json + DOC -> audit.json/.md
    procap run     VIDEO [--against DOC]          all stages end to end
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from typing import NamedTuple

from . import extract as extract_stage
from . import golden as golden_stage
from . import procedure as procedure_stage
from . import audit as audit_stage
from .model import AuditReport, Keyframe, Procedure, Segment
from .run import Run


def _resolve_run(arg: str) -> Run:
    return Run(arg)


class PipelineResult(NamedTuple):
    run: Run
    keyframes: list[Keyframe]
    segments: list[Segment]
    procedure: Procedure
    report: AuditReport | None


def run_pipeline(video: str | Path, against: str | Path | None = None,
                 fps: float = 2.0, run: Run | None = None) -> PipelineResult:
    """Full pipeline (video -> keyframes -> segments -> procedure [-> audit]).

    The single source of truth for an end-to-end run, shared by `procap run` and the
    web demo's upload path (procap.webdemo). Writes all artifacts to the run dir and
    returns the in-memory objects so callers can report without re-reading. Quiet:
    callers print their own summaries.
    """
    run = run or Run.for_video(video)
    extract_stage.extract_keyframes(video, run=run, fps=fps)
    kfs = run.read_keyframes()
    segs = golden_stage.refine_with_vlm(golden_stage.classify(kfs), kfs)
    run.write_segments(segs)
    proc = procedure_stage.synthesize(segs, source_video=str(video), keyframes=kfs)
    run.write_procedure(proc)
    (run.dir / "procedure.md").write_text(procedure_stage.render_markdown(proc))
    run.write_meta(**{**run.read_meta(), "max_active_s": procedure_stage.DEFAULT_MAX_ACTIVE_S})
    report = None
    if against:
        report = audit_stage.audit(proc, against)
        run.write_audit(report)
        (run.dir / "audit.md").write_text(audit_stage.render_markdown(report))
        run.write_meta(**{**run.read_meta(), "match_floor": audit_stage.DEFAULT_MATCH_FLOOR})
    return PipelineResult(run, kfs, segs, proc, report)


def cmd_extract(args) -> int:
    run = Run(args.run) if args.run else Run.for_video(args.video)
    kfs = extract_stage.extract_keyframes(args.video, run=run, fps=args.fps)
    print(f"extracted {len(kfs)} keyframes -> {run.dir}/keyframes.json")
    return 0


def cmd_golden(args) -> int:
    run = _resolve_run(args.run)
    kfs = run.read_keyframes()
    segs = golden_stage.classify(kfs)
    segs = golden_stage.refine_with_vlm(segs, kfs)
    run.write_segments(segs)
    g = sum(1 for s in segs if s.kind == "golden")
    print(f"classified {len(segs)} segments ({g} golden) -> {run.dir}/segments.json")
    return 0


def cmd_procedure(args) -> int:
    run = _resolve_run(args.run)
    meta = run.read_meta()
    segs = run.read_segments()
    proc = procedure_stage.synthesize(segs, source_video=meta["source_video"],
                                      keyframes=run.read_keyframes())
    run.write_procedure(proc)
    (run.dir / "procedure.md").write_text(procedure_stage.render_markdown(proc))
    run.write_meta(**{**meta, "max_active_s": procedure_stage.DEFAULT_MAX_ACTIVE_S})
    print(f"synthesized {len(proc.steps)} steps (~{proc.total_est_seconds:.0f}s) "
          f"-> {run.dir}/procedure.md")
    return 0


def cmd_audit(args) -> int:
    run = _resolve_run(args.run)
    proc = run.read_procedure()
    report = audit_stage.audit(proc, args.against)
    run.write_audit(report)
    (run.dir / "audit.md").write_text(audit_stage.render_markdown(report))
    run.write_meta(**{**run.read_meta(), "match_floor": audit_stage.DEFAULT_MATCH_FLOOR})
    print(f"audit [{report.method}]: {report.coverage * 100:.0f}% coverage, "
          f"{len(report.findings)} finding(s) -> {run.dir}/audit.md")
    return 0


def cmd_run(args) -> int:
    res = run_pipeline(args.video, against=args.against, fps=args.fps)
    g = sum(1 for s in res.segments if s.kind == "golden")
    print(f"[run] {len(res.keyframes)} keyframes, {g} golden segments, "
          f"{len(res.procedure.steps)} steps -> {res.run.dir}")
    if res.report is not None:
        print(f"[run] audit [{res.report.method}]: {res.report.coverage * 100:.0f}% coverage, "
              f"{len(res.report.findings)} finding(s)")
    return 0


def cmd_serve(args) -> int:
    from . import webdemo
    webdemo.serve(args.runs, args.host, args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="procap", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="video -> keyframes")
    pe.add_argument("video")
    pe.add_argument("--fps", type=float, default=2.0)
    pe.add_argument("--run", default=None, help="run dir (default runs/<video-stem>)")
    pe.set_defaults(func=cmd_extract)

    pg = sub.add_parser("golden", help="keyframes -> golden/dross segments")
    pg.add_argument("run", help="run dir")
    pg.set_defaults(func=cmd_golden)

    pp = sub.add_parser("procedure", help="segments -> procedure")
    pp.add_argument("run", help="run dir")
    pp.set_defaults(func=cmd_procedure)

    pa = sub.add_parser("audit", help="procedure vs written doc")
    pa.add_argument("run", help="run dir")
    pa.add_argument("--against", required=True, help="written procedure (markdown/plain)")
    pa.set_defaults(func=cmd_audit)

    pr = sub.add_parser("run", help="all stages end to end")
    pr.add_argument("video")
    pr.add_argument("--fps", type=float, default=2.0)
    pr.add_argument("--against", default=None, help="optional written doc to audit against")
    pr.set_defaults(func=cmd_run)

    ps = sub.add_parser("serve", help="local web demo of run artifacts")
    ps.add_argument("--runs", default="runs", help="base dir holding run dirs (default runs/)")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8000)
    ps.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
