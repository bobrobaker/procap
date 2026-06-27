"""A dependency-free local web demo of the procap pipeline.

Reads the on-disk artifacts a run produces (see procap.run) and renders one of two
purpose-distinct demos, chosen structurally by RunView.mode:
  - Demo A (note-taking): document an existing procedure into an SOP. ProCap guesses
    golden/dross and drafts timed steps; the operator retags wrong guesses, annotates
    each step, adds off-screen steps, and exports a PDF.
  - Demo B (conformance): qualify a recording against a *provided* SOP. ProCap reports
    where they diverge; the reviewer confirms or dismisses each flag and exports a PDF.
Both walk the pipeline stages (keyframes -> golden/dross -> procedure [-> audit]) so the
*judgement* stays legible. Edits are in-memory only (no write path); the PDF is the
durable output, produced by the browser's print-to-PDF over the live edited DOM.

No web framework: stdlib http.server + a small vanilla-JS layer, so the demo carries
zero dependency (procap's heuristics are the always-on baseline; the demo should be too).

Run it:
    python -m procap.webdemo                 # serve every run under runs/
    python -m procap.webdemo --run runs/foo  # serve one run
    procap serve                             # same, via the CLI
"""
from __future__ import annotations

import html
import json
import re
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

from .model import (
    Procedure, Segment, Keyframe, AuditReport, SegmentKind, FindingKind, AuditMethod,
)
from .audit import parse_written_steps
from .eval import score_against_labels
from .run import Run

EVAL_STEP = 0.1  # time-grid resolution the scorer uses; reused to convert grid points -> seconds

# Upload limits (the demo accepts user recordings via POST /run — see make_handler).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024            # cap the in-memory request body
MAX_UPLOADS_KEPT = 5                            # prune older upload_* runs beyond this
_ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_DISPOSITION_RE = re.compile(rb'name="([^"]*)"(?:; *filename="([^"]*)")?')

# ---------------------------------------------------------------------------
# Artifact loading (tolerant: a run mid-pipeline is missing later stages)
# ---------------------------------------------------------------------------


def _discover_runs(base: Path) -> list[Path]:
    """Run dirs are any subdir of base that has a keyframes.json."""
    if not base.exists():
        return []
    runs = [p for p in sorted(base.iterdir()) if (p / "keyframes.json").exists()]
    return runs


def _load_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


class RunView:
    """Everything the page needs about one run, with later stages optional."""

    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.name = run_dir.name
        self.meta: dict = _load_json(run_dir / "meta.json") or {}
        kf = _load_json(run_dir / "keyframes.json") or []
        self.keyframes: list[Keyframe] = [Keyframe.from_dict(d) for d in kf]
        seg = _load_json(run_dir / "segments.json")
        self.segments: list[Segment] | None = (
            [Segment.from_dict(d) for d in seg] if seg is not None else None
        )
        proc = _load_json(run_dir / "procedure.json")
        self.procedure: Procedure | None = (
            Procedure.from_dict(proc) if proc is not None else None
        )
        aud = _load_json(run_dir / "audit.json")
        self.audit: AuditReport | None = (
            AuditReport.from_dict(aud) if aud is not None else None
        )
        self.labels, self.labels_path = self._load_labels()

    def _load_labels(self) -> tuple[list[dict] | None, Path | None]:
        """Ground-truth golden/dross spans, if the source video has a sibling
        `<stem>.labels.json` (the synthetic corpus ships these). Lets the demo show
        measured accuracy on the one run where truth exists instead of asserting it."""
        src = self.meta.get("source_video")
        if not src:
            return None, None
        p = Path(src)
        cand = p.parent / (p.stem + ".labels.json")
        return _load_json(cand), cand

    @property
    def vlm_active(self) -> bool:
        """Did the VLM enrich this run, or is it the heuristic-only floor? True iff any
        segment was VLM-judged. (Titles/semantic-audit only appear when a key is present.)"""
        if not self.segments:
            return False
        return any(s.judged_by == "vlm" for s in self.segments)

    @property
    def mode(self) -> str:
        """Which of the two demos this run drives, decided structurally (not by name):
        - "conformance": the run carries an audit against a provided SOP — the workflow is
          qualifying a recording against that doc. The audit *method* (vlm/lexical/count)
          bounds what the review can find, and _render_audit states that honestly; even a
          count-only audit belongs here, because the user provided an SOP to check against.
        - "notetaking": no audit at all — the workflow is documenting a recording into a new
          SOP, guessing golden/dross for the operator to retag and annotate.

        Rationale (2026-06-27): previously a count-only audit routed to note-taking, which hid
        an uploaded user's SOP entirely when no VLM key was set. Routing any audited run to
        conformance keeps the user's intent (check against my SOP) and shows the doc + an
        honest 'needs a VLM for content matching' note instead of silently dropping it."""
        return "conformance" if self.audit is not None else "notetaking"

    @property
    def keyframe_by_index(self) -> dict[int, Keyframe]:
        return {k.index: k for k in self.keyframes}

    def written_doc_text(self) -> str | None:
        """The body of the SOP this run was audited against, or None if unavailable.

        `AuditReport.written_doc` is only a path. Resolve it robustly: as given (cwd),
        then by basename inside the run dir (where the upload path saves a copy), so the
        reference document renders regardless of the server's working directory."""
        if not self.audit or not self.audit.written_doc:
            return None
        for cand in (Path(self.audit.written_doc), self.dir / Path(self.audit.written_doc).name):
            try:
                if cand.exists():
                    return cand.read_text()
            except OSError:
                continue
        return None

    @property
    def duration(self) -> float:
        if self.meta.get("duration"):
            return float(self.meta["duration"])
        return self.keyframes[-1].t_end if self.keyframes else 0.0


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

CSS = """
:root {
  --golden: #d4a017; --golden-bg: #fff8e6; --golden-line: #b8860b;
  --dross: #9aa0a6; --dross-bg: #f1f3f4;
  --ink: #1f2328; --muted: #57606a; --line: #d8dee4; --bg: #f6f8fa;
  --accent: #0969da; --good: #1a7f37; --warn: #9a6700; --bad: #cf222e;
}
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: var(--ink); background: var(--bg); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header.top { background: #0d1117; color: #e6edf3; padding: 18px 28px; }
header.top h1 { margin: 0; font-size: 20px; letter-spacing: .3px; }
header.top .tag { color: #8b949e; font-size: 13px; margin-top: 3px; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 24px 28px 80px; }
.stage { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 22px 24px; margin-bottom: 22px; }
.stage > h2 { margin: 0 0 4px; font-size: 17px; display: flex; align-items: baseline; gap: 10px; }
.stage > h2 .n { color: #fff; background: var(--accent); border-radius: 6px; font-size: 12px; padding: 2px 8px; }
.stage > .lede { color: var(--muted); margin: 0 0 18px; font-size: 13.5px; }
.statgrid { display: flex; flex-wrap: wrap; gap: 22px; }
.stat { }
.stat .v { font-size: 24px; font-weight: 650; }
.stat .l { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .4px; }

/* filmstrip */
.film { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 8px; }
.frame { flex: 0 0 auto; width: 150px; }
.frame img { width: 150px; height: 94px; object-fit: cover; border-radius: 8px; border: 2px solid var(--line); background: #000; display: block; }
.frame.golden img { border-color: var(--golden); }
.frame.dross img { border-color: var(--dross); opacity: .55; }
.frame .cap { font-size: 11.5px; color: var(--muted); margin-top: 5px; }
.frame .cap b { color: var(--ink); }

/* timeline */
.timeline { position: relative; height: 56px; border-radius: 8px; overflow: hidden; border: 1px solid var(--line); display: flex; }
.seg { position: relative; display: flex; align-items: center; justify-content: center; font-size: 11px; color: #fff; overflow: hidden; cursor: default; }
.seg.golden { background: var(--golden); }
.seg.dross { background: var(--dross); }
.seg .lbl { padding: 0 4px; white-space: nowrap; text-shadow: 0 1px 1px rgba(0,0,0,.3); }
.axis { display: flex; justify-content: space-between; color: var(--muted); font-size: 11px; margin-top: 4px; }
.legend { display: flex; gap: 16px; margin: 14px 0 6px; font-size: 12.5px; }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -1px; margin-right: 5px; }
.segtable { width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 13px; }
.segtable th { text-align: left; color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .4px; padding: 4px 8px; border-bottom: 1px solid var(--line); }
.segtable td { padding: 7px 8px; border-bottom: 1px solid var(--bg); vertical-align: top; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.pill.golden { background: var(--golden-bg); color: var(--golden-line); }
.pill.dross { background: var(--dross-bg); color: var(--muted); }

/* procedure */
.step { display: flex; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--bg); }
.step:last-child { border-bottom: none; }
.step .num { flex: 0 0 30px; height: 30px; border-radius: 50%; background: var(--ink); color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 650; font-size: 14px; }
.step .body { flex: 1; }
.step .body h3 { margin: 2px 0 3px; font-size: 15px; }
.step .body .meta { color: var(--muted); font-size: 12.5px; }
.step .body .intent { margin-top: 6px; font-size: 13px; }
.step .thumbs { display: flex; gap: 6px; }
.step .thumbs img { width: 92px; height: 58px; object-fit: cover; border-radius: 6px; border: 1px solid var(--line); }
.todo { color: var(--warn); font-style: italic; }
.held { color: var(--warn); }

/* audit */
.cov { height: 22px; border-radius: 999px; background: var(--dross-bg); overflow: hidden; }
.cov > span { display: block; height: 100%; background: var(--good); }
.finding { padding: 10px 12px; border-left: 3px solid var(--warn); background: #fffaf0; border-radius: 0 8px 8px 0; margin-top: 10px; font-size: 13.5px; }
.finding .k { font-weight: 650; color: var(--warn); text-transform: uppercase; font-size: 11px; letter-spacing: .4px; }
.findgroup { font-size: 13px; margin: 18px 0 2px; cursor: help; }
.findgroup .muted { font-weight: 400; }
.auditcount { font-size: 15px; margin: 6px 0 4px; }
.empty { color: var(--muted); font-style: italic; }

/* reference SOP panel — the document being checked, rendered on the page */
.sopdoc { border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px 14px; margin: 14px 0 4px; background: #fcfcfd; }
.sopdoc h3 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
.sopdoc ol { margin: 0; padding-left: 26px; font-size: 13.5px; }
.sopdoc li { margin: 4px 0; padding: 2px 6px; border-radius: 6px; }
.sopdoc li.flag { background: #fffaf0; box-shadow: inset 3px 0 0 var(--warn); }
.sopdoc li .ftag { display: inline-block; margin-left: 8px; font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--warn); }
/* per-finding side-by-side: written doc vs what the recording showed */
.finding .fcols { display: flex; gap: 14px; margin-top: 8px; align-items: flex-start; }
.finding .fcol { flex: 1 1 0; min-width: 0; }
.finding .fcol .hdr { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); margin-bottom: 3px; }
.finding .recbody { display: flex; gap: 9px; align-items: flex-start; }
.finding .recbody img { width: 104px; height: 65px; object-fit: cover; border-radius: 6px; border: 2px solid var(--golden); flex: 0 0 auto; }
.finding .nofrm { color: var(--muted); font-style: italic; font-size: 12.5px; }
.finding .why { margin-top: 8px; font-size: 12.5px; color: var(--muted); }
@media (max-width: 640px) { .finding .fcols { flex-direction: column; } }
.badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .3px; vertical-align: 1px; }
.badge.vlm-on { background: #1a7f37; color: #fff; }
.badge.vlm-off { background: #3a3f44; color: #ffd33d; border: 1px solid #ffd33d; }
.badge-note { color: #8b949e; font-size: 11px; margin-left: 8px; }
footer { color: var(--muted); font-size: 12px; text-align: center; padding: 20px; }

.frame a { display: block; cursor: zoom-in; }
.muted { color: var(--muted); }

/* :target lightbox — no JS; click a keyframe to audit its verdict */
.lightbox { position: fixed; inset: 0; background: rgba(13,17,23,.82); display: none; align-items: center; justify-content: center; z-index: 60; padding: 24px; cursor: zoom-out; }
.lightbox:target, .lightbox.show { display: flex; }
.lightbox .box { background: #fff; border-radius: 12px; padding: 16px 18px 18px; max-width: 880px; width: 100%; max-height: 90vh; overflow: auto; }
.lightbox .box img { width: 100%; max-height: 60vh; object-fit: contain; border-radius: 8px; background: #000; }
.lightbox .close { float: right; font-size: 20px; color: var(--muted); text-decoration: none; line-height: 1; padding: 2px 6px; }
.lightbox .lbmeta h3 { margin: 12px 0 6px; font-size: 16px; }
.lightbox .lbmeta p { margin: 4px 0; font-size: 13.5px; }

/* progressive-disclosure layer (how it works, FAQ) */
.disc { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 6px 24px; margin-bottom: 22px; }
.disc > summary { cursor: pointer; font-size: 17px; font-weight: 650; padding: 14px 0; list-style: none; }
.disc > summary::-webkit-details-marker { display: none; }
.disc > summary::before { content: "▸ "; color: var(--accent); }
.disc[open] > summary::before { content: "▾ "; }
.disc .lede { margin-top: 0; }
ol.how { margin: 4px 0 16px; padding-left: 22px; }
ol.how li { margin: 7px 0; font-size: 14px; }
.howtrace { font-size: 13.5px; background: var(--dross-bg); border-radius: 8px; padding: 10px 12px; margin: 0 0 16px; }
.howstage { border-left: 3px solid var(--line); padding: 2px 0 2px 14px; margin: 14px 0; font-size: 13.5px; }
.howstage h3 { margin: 4px 0 6px; font-size: 15px; }
.howstage h3 .n { display: inline-block; min-width: 20px; color: var(--accent); font-weight: 700; }
.howstage p { margin: 5px 0; }
.howio { color: var(--muted); }
.howknob code { font-size: 12px; background: var(--dross-bg); padding: 1px 6px; border-radius: 5px; }
.howlimit { color: var(--warn); }
.howlink { font-size: 12px; font-weight: 400; margin-left: 6px; }
dl.faq { margin: 4px 0 16px; }
dl.faq dt { font-weight: 650; margin-top: 14px; font-size: 14.5px; }
dl.faq dd { margin: 4px 0 0; color: #2c333a; font-size: 13.8px; }

/* product-level "About ProCap" card: why + term gloss + nested how/faq */
.about { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 18px 24px; margin: 0 0 20px; }
.about > h2 { margin: 0 0 6px; font-size: 18px; }
.about .why { font-size: 14px; color: var(--ink); margin: 0 0 12px; max-width: 76ch; }
/* golden/dross gloss — a one-liner each, not a header-sized block */
.gloss { display: flex; gap: 22px; flex-wrap: wrap; margin: 0; font-size: 13px; }
.gloss span { color: var(--muted); }
.gloss b { color: var(--ink); }
.gloss b i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: 0; }

/* upload: run your own recording */
.upload { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 18px 24px; margin: 0 0 20px; }
.upload > h2 { margin: 0 0 4px; font-size: 18px; }
.upload .lede { color: var(--muted); font-size: 13.5px; margin: 0 0 14px; max-width: 80ch; }
.upload .row { display: flex; gap: 22px; flex-wrap: wrap; align-items: flex-start; }
.upload .fld { flex: 1 1 300px; }
.upload label { display: block; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin: 0 0 5px; }
.upload input[type=file] { font: inherit; font-size: 13px; max-width: 100%; }
.upload textarea { width: 100%; min-height: 110px; font: inherit; font-size: 13px; border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; resize: vertical; }
.upload .hint { font-size: 12px; color: var(--muted); margin: 5px 0 0; }
.upload .actions { margin-top: 16px; }
#proc-overlay { position: fixed; inset: 0; background: rgba(13,17,23,.86); color: #e6edf3; display: none; align-items: center; justify-content: center; flex-direction: column; gap: 16px; text-align: center; padding: 24px; z-index: 80; }
#proc-overlay.show { display: flex; }
#proc-overlay .spin { width: 42px; height: 42px; border: 4px solid rgba(255,255,255,.25); border-top-color: #fff; border-radius: 50%; animation: spin 1s linear infinite; }
#proc-overlay .sub { font-size: 13px; color: #8b949e; }
@keyframes spin { to { transform: rotate(360deg); } }

/* nested collapsibles inside about/demoblock shed their own card chrome */
.about .disc, .demoblock .disc { border: none; border-top: 1px solid var(--line); border-radius: 0; padding: 0; margin: 6px 0 0; background: transparent; }
.about .disc > summary, .demoblock .disc > summary { font-size: 14px; padding: 11px 0 7px; }

/* one card per demo: header (intro) + the output + an evidence collapsible — no box-in-box */
.demoblock { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 18px 24px 10px; margin: 0 0 22px; }
.demoblock .demointro { border: none; border-radius: 0; padding: 0; margin: 0 0 16px; background: transparent; }
.demoblock .demointro h2 { font-size: 18px; }
.demoblock .stage { border: none; border-radius: 0; padding: 0; margin: 0 0 16px; background: transparent; }
.demoblock .disc .stage:last-child { margin-bottom: 8px; }
.metaline { margin: 12px 0 6px; }
.metaline .tag { color: var(--muted); font-size: 12px; }

/* demo chooser + per-demo intro */
.chooser { display: flex; gap: 10px; flex-wrap: wrap; margin: 0 0 8px; }
.chooser a { flex: 1 1 320px; border: 1px solid var(--line); border-radius: 12px; padding: 14px 18px; background: #fff; color: var(--ink); }
.chooser a.active { border-color: var(--ink); box-shadow: inset 0 0 0 1px var(--ink); }
.chooser a .dlabel { font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: var(--accent); font-weight: 700; }
.chooser a.active .dlabel { color: var(--ink); }
.chooser a .dname { display: block; font-size: 15.5px; font-weight: 650; margin-top: 2px; }
.chooser a .dsub { display: block; font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.demointro { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 20px 24px; margin: 0 0 22px; }
.demointro h2 { margin: 0 0 6px; font-size: 19px; }
.demointro .purpose { font-size: 14.5px; color: var(--ink); margin: 0 0 12px; max-width: 72ch; }
.demointro .purpose b { color: var(--ink); }
.demointro .steps-of { display: flex; gap: 8px; flex-wrap: wrap; }
.demointro .steps-of span { background: var(--bg); border: 1px solid var(--line); border-radius: 999px; padding: 5px 13px; font-size: 12.5px; color: var(--muted); }
.demointro .steps-of b { color: var(--ink); margin-right: 4px; }
.demointro .proof { margin: 10px 0 0; font-size: 12.5px; color: var(--muted); }
.demointro .proof b { color: var(--good); }

/* toolbar (export / add-blank) */
.toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin: 0 0 16px; }
.btn { border: 1px solid var(--line); background: #fff; color: var(--ink); border-radius: 8px; padding: 7px 14px; font-size: 13px; cursor: pointer; font: inherit; }
.btn:hover { background: var(--bg); }
.btn.primary { background: var(--ink); color: #fff; border-color: var(--ink); }
.btn.primary:hover { background: #2c333a; }

/* editable procedure step (note-taking mode) */
.step.dross { opacity: .62; }
.step.dross .num { background: var(--dross); }
.step .tagrow { display: flex; align-items: center; gap: 10px; margin: 4px 0 0; }
.step .tag-state { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; padding: 1px 8px; border-radius: 999px; }
.step.golden .tag-state { background: var(--golden-bg); color: var(--golden-line); }
.step.dross .tag-state { background: var(--dross-bg); color: var(--muted); }
.retag { border: 1px solid var(--line); background: #fff; border-radius: 7px; padding: 3px 10px; font-size: 12px; cursor: pointer; font: inherit; color: var(--accent); }
.retag:hover { background: var(--bg); }
.notes { display: block; width: 100%; margin-top: 9px; border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font: inherit; font-size: 13px; resize: vertical; min-height: 38px; color: var(--ink); background: #fff; }
.notes::placeholder { color: #9aa0a6; }
.step .noframe { width: 92px; height: 58px; border-radius: 6px; border: 1px dashed var(--line); display: flex; align-items: center; justify-content: center; text-align: center; font-size: 10px; color: var(--muted); background: var(--bg); padding: 4px; }
.blankstep .body h3 { color: var(--muted); }

/* confirm/deny verdict (conformance mode) */
.finding .verdict { display: flex; gap: 8px; margin-top: 9px; align-items: center; }
.vbtn { border: 1px solid var(--line); background: #fff; border-radius: 7px; padding: 3px 12px; font-size: 12px; cursor: pointer; font: inherit; }
.vbtn:hover { background: var(--bg); }
.vbtn.on-confirm.active { background: var(--bad); color: #fff; border-color: var(--bad); }
.vbtn.on-deny.active { background: var(--good); color: #fff; border-color: var(--good); }
.vstate { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
.finding.confirmed { border-left-color: var(--bad); }
.finding.denied { border-left-color: var(--good); background: #f2fbf4; }
.finding.denied .vstate { color: var(--good); }
.finding.confirmed .vstate { color: var(--bad); }

/* print → PDF: only the edited procedure (A) / reviewed findings (B) — screen frame + notes
   per step, nothing else. Everything but the active editor/review section is dropped. */
@media print {
  body { background: #fff; }
  .no-print { display: none !important; }
  header.top, footer, .about, .chooser, .metaline, .demointro, .disc { display: none !important; }
  .stage:not([data-editor]):not([data-review]) { display: none !important; }
  .wrap { max-width: none; padding: 0; }
  .demoblock { border: none; border-radius: 0; padding: 0; margin: 0; background: #fff; }
  .stage { border: none; border-radius: 0; padding: 0; margin: 0; }
  .stage > h2 { display: none; }
  .step { break-inside: avoid; page-break-inside: avoid; border-bottom: 1px solid #ddd; }
  .step.dross { display: none !important; }  /* retagged-out steps leave the exported SOP */
  .step.dross .num { background: var(--ink); }
  .finding.denied { display: none !important; }  /* dismissed gaps leave the exported review */
  .sopdoc { background: #fff; }
  .notes { border: none; padding: 4px 0; min-height: 0; resize: none; }
  .finding { break-inside: avoid; page-break-inside: avoid; }
  .printtitle { display: block !important; font-size: 22px; font-weight: 700; margin: 0 0 4px; }
  .printmeta { display: block !important; color: #555; font-size: 12px; margin: 0 0 18px; }
  a[href]::after { content: ""; }  /* don't print URLs after links */
}
.printtitle, .printmeta { display: none; }
"""


def _esc(s) -> str:
    return html.escape(str(s))


def _fmt_t(t: float) -> str:
    t = float(t)
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:04.1f}" if m else f"{s:.1f}s"


def _img_src(run_name: str, kf: Keyframe) -> str:
    # serve via /img?run=<name>&file=<basename>
    return f"/img?run={quote(run_name)}&file={quote(Path(kf.path).name)}"


def _golden_kf_indexes(rv: RunView) -> set[int]:
    out: set[int] = set()
    if rv.segments:
        for s in rv.segments:
            if s.kind == SegmentKind.GOLDEN:
                out.update(s.keyframe_indexes)
    return out


def _kf_segment_map(rv: RunView) -> dict[int, Segment]:
    out: dict[int, Segment] = {}
    for s in rv.segments or []:
        for i in s.keyframe_indexes:
            out[i] = s
    return out


def _kf_step_map(rv: RunView) -> dict[int, int]:
    """keyframe index -> 1-based procedure step number it became (golden frames only)."""
    out: dict[int, int] = {}
    if rv.procedure:
        for st in rv.procedure.steps:
            for i in st.keyframe_indexes:
                out[i] = st.index + 1
    return out


def _render_keyframes(rv: RunView) -> str:
    golden = _golden_kf_indexes(rv)
    classified = rv.segments is not None
    frames = []
    for kf in rv.keyframes:
        cls = ""
        if classified:
            cls = "golden" if kf.index in golden else "dross"
        cap = (
            f'<div class="cap"><b>#{kf.index}</b> · {_fmt_t(kf.t)}'
            f'<br>Δ {kf.change_score:.3f}'
            f'{" · click" if kf.click_detected else ""}</div>'
        )
        # clickable: anchor to a lightbox (id emitted by _keyframe_lightboxes at page level)
        frames.append(
            f'<div class="frame {cls}"><a href="#kf-{rv.name}-{kf.index}" data-lb="kf-{rv.name}-{kf.index}">'
            f'<img loading="lazy" src="{_img_src(rv.name, kf)}" alt="keyframe {kf.index}"></a>'
            f"{cap}</div>"
        )
    lede = (
        "Every frame the screen durably changed <em>to</em>, sampled and de-duplicated "
        "by perceptual-hash diff. Border colour shows the stage-2 verdict (gold = kept, "
        "grey = dropped). <b>Click any frame</b> to see why it was kept or dropped and which "
        "step it became."
    )
    return (
        '<section class="stage" id="stage-keyframes"><h2>Keyframes</h2>'
        f'<p class="lede">{lede}</p>'
        f'<div class="film">{"".join(frames)}</div></section>'
    )


def _keyframe_lightboxes(rv: RunView) -> str:
    """The per-keyframe verdict overlays, emitted at PAGE level so they work even when the
    keyframe filmstrip lives inside a collapsed <details> (a fixed overlay inside a closed
    <details> is hidden). Both the filmstrip and the procedure thumbnails anchor to these."""
    seg_of = _kf_segment_map(rv)
    step_of = _kf_step_map(rv)
    boxes = []
    for kf in rv.keyframes:
        seg = seg_of.get(kf.index)
        if seg is not None:
            if kf.index in step_of:
                verdict = f'<span class="pill golden">KEPT</span> → became <b>step {step_of[kf.index]}</b>'
            else:
                verdict = '<span class="pill dross">DROPPED</span> as dross'
            conf_title = (
                "Per-segment certainty (~0.5–0.95) from the margin of the heuristic's own "
                "decision: a bigger/cleaner change or a cleaner revert scores higher."
            )
            why = (
                f"<p><b>Why:</b> {_esc(seg.reason)} "
                f"<span class='muted' title=\"{conf_title}\">"
                f"(judged by {_esc(seg.judged_by)}, conf {seg.confidence:.2f})</span></p>"
            )
        else:
            verdict, why = "<span class='muted'>not yet classified</span>", ""
        delta_title = (
            "Changed-pixel fraction vs the previous kept keyframe (0–1): how much of the "
            "screen this frame altered. A larger Δ is a bigger, cleaner state change."
        )
        boxes.append(
            f'<div class="lightbox" id="kf-{rv.name}-{kf.index}"><div class="box">'
            f'<a class="close" href="#" data-lbclose>✕</a>'
            f'<img src="{_img_src(rv.name, kf)}" alt="keyframe {kf.index} enlarged">'
            f'<div class="lbmeta"><h3>Keyframe #{kf.index} · {_fmt_t(kf.t)}–{_fmt_t(kf.t_end)}</h3>'
            f"<p>{verdict}</p>{why}"
            f'<p class="muted" title="{delta_title}">change Δ {kf.change_score:.3f} vs previous kept frame'
            f'{" · click detected" if kf.click_detected else ""}</p></div></div></div>'
        )
    return "".join(boxes)


def _render_segments(rv: RunView) -> str:
    if rv.segments is None:
        return (
            '<section class="stage"><h2>Golden / dross</h2>'
            '<p class="empty">Not run yet — run the <code>golden</code> stage.</p></section>'
        )
    total = rv.duration or sum(s.duration for s in rv.segments) or 1.0
    bars = []
    for s in rv.segments:
        pct = max(2.0, 100.0 * s.duration / total)
        kind = "golden" if s.kind == SegmentKind.GOLDEN else "dross"
        bars.append(
            f'<div class="seg {kind}" style="flex:0 0 {pct:.3f}%" '
            f'title="{_esc(s.reason)}"><span class="lbl">{_fmt_t(s.duration)}</span></div>'
        )
    # Show the "By" column only when the VLM actually judged something. Offline it is a dead
    # constant ("heuristic" on every row), so collapse it into the lede instead of a column.
    show_by = rv.vlm_active
    rows = []
    for s in rv.segments:
        kind = "golden" if s.kind == SegmentKind.GOLDEN else "dross"
        by_cell = f"<td>{_esc(s.judged_by)}</td>" if show_by else ""
        rows.append(
            "<tr>"
            f'<td>{_fmt_t(s.start_t)}–{_fmt_t(s.end_t)}</td>'
            f'<td><span class="pill {kind}">{_esc(s.kind)}</span></td>'
            f"<td>{_esc(s.reason)}</td>"
            f"<td>{s.confidence:.2f}</td>"
            f"{by_cell}"
            "</tr>"
        )
    lede = (
        "Contiguous stretches classified <b>golden</b> (a consequential action worth "
        "keeping) or <b>dross</b> (a mis-click reverted, mouse wander, dead time). The "
        "revert-detection heuristic is always on; a VLM refines it when a key is present."
    )
    if not show_by:
        lede += " Every row here was judged by the heuristic (no VLM key set)."
    legend = (
        '<div class="legend">'
        '<span><i style="background:var(--golden)"></i>golden — kept</span>'
        '<span><i style="background:var(--dross)"></i>dross — dropped</span></div>'
    )
    # Each header explains HOW its column is computed (hover for the tooltip).
    th = (
        '<th title="Start–end time of this stretch within the recording.">Span</th>'
        '<th title="golden = a consequential action, kept as a procedure step; '
        'dross = reverted / idle / mouse-wander, dropped.">Kind</th>'
        '<th title="Which heuristic made the call: a revert back to an earlier state, '
        'a too-brief hold, or a durable change kept as consequential.">Reason</th>'
        '<th title="Per-segment certainty (~0.5–0.95) from the margin of the heuristic\'s '
        "own decision: a bigger/cleaner change or a cleaner revert scores higher. Below 0.8 a "
        'VLM second opinion is sought when an API key is present.">Conf.</th>'
    )
    if show_by:
        th += ('<th title="Which judge set this row: the always-on heuristic, or a VLM that '
               'refined an ambiguous (&lt;0.8) call.">By</th>')
    table = (
        '<table class="segtable"><thead><tr>'
        f"{th}"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return (
        '<section class="stage" id="stage-golden"><h2>Golden / dross</h2>'
        f'<p class="lede">{lede}</p>'
        f'{legend}<div class="timeline">{"".join(bars)}</div>'
        f'<div class="axis"><span>0:00</span><span>{_fmt_t(total)}</span></div>'
        f"{table}</section>"
    )


def _spans_strip(spans: list[tuple[float, float, str]], total: float) -> str:
    """A bare golden/dross timeline bar from (start, end, kind) spans — no duration labels.
    Used to stack ground-truth above prediction so disagreement is visible at a glance."""
    bars = []
    for start, end, kind in spans:
        pct = max(1.0, 100.0 * (end - start) / total)
        k = "golden" if kind == SegmentKind.GOLDEN.value else "dross"
        bars.append(
            f'<div class="seg {k}" style="flex:0 0 {pct:.3f}%" '
            f'title="{_esc(kind)} {_fmt_t(start)}–{_fmt_t(end)}"></div>'
        )
    return f'<div class="timeline">{"".join(bars)}</div>'


def _render_eval(rv: RunView) -> str:
    """Measured accuracy of the golden/dross call against ground truth — only for a run
    whose source has a sibling labels file. This is the honest core of the demo: on the one
    run where truth exists, show the score instead of asserting the judgement works."""
    if rv.segments is None or not rv.labels:
        return ""
    sc = score_against_labels(rv.segments, rv.labels)
    truth = [(l["start"], l["end"], l["kind"]) for l in rv.labels]
    pred = [(s.start_t, s.end_t, s.kind) for s in rv.segments]
    total = max([rv.duration] + [e for _s, e, _k in truth]) or 1.0
    fp_s = round(sc.get("fp", 0) * EVAL_STEP, 1)
    fn_s = round(sc.get("fn", 0) * EVAL_STEP, 1)
    # honest, data-derived interpretation — no hard-coded story
    notes = []
    if fp_s:
        notes.append(
            f"<b>{fp_s}s</b> the heuristic kept as <span class='pill golden'>golden</span> "
            "that ground truth calls dross — the revert heuristic's blind spot for "
            "non-reverting dross (idle / mouse-wander / dead time), which then leaks into "
            "step time estimates."
        )
    if fn_s:
        notes.append(
            f"<b>{fn_s}s</b> of true golden the heuristic dropped as dross."
        )
    if not notes:
        notes.append("No disagreement on this clip.")
    metric_help = {
        "precision": (f"Precision = tp / (tp + fp): of the time graded golden, the fraction "
                      f"ground truth agrees is golden. Computed on a {EVAL_STEP}s grid."),
        "recall": (f"Recall = tp / (tp + fn): of the true golden time, the fraction the "
                   f"heuristic recovered. Computed on a {EVAL_STEP}s grid."),
        "f1": "F1 = harmonic mean of precision and recall — one combined score.",
    }
    metrics = "".join(
        f'<div class="stat" title="{metric_help[k]}"><div class="v">{sc.get(k, 0):.2f}</div>'
        f'<div class="l">{lbl}</div></div>'
        for k, lbl in (("precision", "Precision"), ("recall", "Recall"), ("f1", "F1"))
    )
    lede = (
        "Stage-2's golden/dross call scored against the corpus ground-truth labels on a "
        f"{EVAL_STEP}s time grid (golden is the positive class). Top strip is truth, bottom "
        "is what the heuristic predicted — gaps between them are the errors below."
    )
    # Threshold honesty: the score is conditional on the knobs, and on ONE labeled clip it
    # is in-sample. Surface both rather than presenting 0.84 as a context-free truth.
    knobs = []
    for k, lbl in (("change_threshold", "change Δ"), ("min_dwell_s", "min dwell"),
                   ("max_active_s", "max active")):
        if k in rv.meta:
            knobs.append(f"{lbl} {rv.meta[k]}")
    knob_line = (
        f'<p style="font-size:12px;color:var(--muted);margin:0 0 10px">'
        f'Thresholds used: {", ".join(knobs)}. '
        if knobs
        else '<p style="font-size:12px;color:var(--muted);margin:0 0 10px">'
    )
    caveat = (
        "<b>In-sample:</b> this is the only labeled clip — these numbers are measured on "
        "the same clip any thresholds were chosen against, so treat them as a sanity check, "
        "not out-of-sample accuracy.</p>"
    )
    return (
        '<section class="stage" id="stage-eval"><h2>Accuracy vs ground truth</h2>'
        f'<p class="lede">{lede}</p>'
        f"{knob_line}{caveat}"
        f'<div class="statgrid" style="margin-bottom:16px">{metrics}</div>'
        '<div style="font-size:11px;color:var(--muted);margin-bottom:3px">GROUND TRUTH</div>'
        f"{_spans_strip(truth, total)}"
        '<div style="font-size:11px;color:var(--muted);margin:8px 0 3px">PREDICTED (heuristic)</div>'
        f"{_spans_strip(pred, total)}"
        f'<div class="axis"><span>0:00</span><span>{_fmt_t(total)}</span></div>'
        + "".join(f'<div class="finding">{n}</div>' for n in notes)
        + f'<p style="font-size:12px;color:var(--muted);margin-top:10px">Labels: '
        f'<code>{_esc(rv.labels_path)}</code> · tp {sc.get("tp",0)} · fp {sc.get("fp",0)} · '
        f'fn {sc.get("fn",0)} · tn {sc.get("tn",0)} grid points.</p>'
        "</section>"
    )


def _est_html(st) -> str:
    """The step's time estimate, splitting out any hold past the active-action cap."""
    if st.held_seconds > 0:
        active = max(0.0, st.est_seconds - st.held_seconds)
        held_title = (
            "est = the full segment span. 'held' = the part beyond the active-action "
            "threshold (max active) that the state just sat unchanged; pixels can't tell "
            "active work from waiting, so it is reported separately, not hidden."
        )
        return (
            f"est {_fmt_t(st.est_seconds)} "
            f'<span class="held" title="{held_title}">(≤{_fmt_t(active)} active + '
            f'{_fmt_t(st.held_seconds)} held — attribution unknown)</span>'
        )
    return f"est {_fmt_t(st.est_seconds)}"


_BLANK_STEP_TEMPLATE = (
    '<template id="blankstep-tpl">'
    '<div class="step golden blankstep" data-step data-kind="golden" data-blank>'
    '<div class="num">+</div>'
    '<div class="body"><h3>Off-screen step</h3>'
    '<div class="meta">no screen frame — manual entry</div>'
    '<div class="tagrow no-print"><span class="tag-state">golden — kept</span>'
    '<button type="button" class="retag" data-retag>Retag as dross</button>'
    '<button type="button" class="retag" data-insert-below>+ Insert step below</button>'
    '<button type="button" class="retag" data-remove>Remove</button></div>'
    '<textarea class="notes" data-notes placeholder="Describe the off-screen work the '
    'recording could not capture (e.g. a physical action at the bench)…"></textarea></div>'
    '<div class="thumbs"><div class="noframe">no screen frame</div></div></div></template>'
)


def _render_procedure(rv: RunView, interactive: bool = False) -> str:
    if rv.procedure is None:
        return (
            '<section class="stage"><h2>Procedure</h2>'
            '<p class="empty">Not run yet — run the <code>procedure</code> stage.</p></section>'
        )
    kbi = rv.keyframe_by_index
    steps = []
    for st in rv.procedure.steps:
        # thumbnails link to the same :target lightboxes the keyframe stage emits, so a user
        # can open "is this really one action?" right where they evaluate the output.
        thumbs = "".join(
            f'<a href="#kf-{rv.name}-{i}" data-lb="kf-{rv.name}-{i}"><img loading="lazy" src="{_img_src(rv.name, kbi[i])}" alt="kf {i}"></a>'
            for i in st.keyframe_indexes
            if i in kbi
        )
        title = _esc(st.title)
        if "[fill in" in st.title:
            title = f'<span class="todo">{title}</span>'
        meta = (
            f'<div class="meta">{_fmt_t(st.start_t)}–{_fmt_t(st.end_t)} · '
            f"{_est_html(st)} · keyframes {', '.join('#'+str(i) for i in st.keyframe_indexes)}</div>"
        )
        if interactive:
            # Note-taking mode: the operator can retag a wrong guess and annotate each step.
            # The notes box is the real input here, so a still-placeholder title shows as a
            # clean "Step N" rather than the raw [fill in] marker.
            notes = _esc(st.intent or "")
            heading = f"Step {st.index + 1}" if "[fill in" in st.title else title
            steps.append(
                '<div class="step golden" data-step data-kind="golden">'
                f'<div class="num">{st.index + 1}</div>'
                '<div class="body">'
                f"<h3>{heading}</h3>{meta}"
                '<div class="tagrow no-print"><span class="tag-state">golden — kept</span>'
                '<button type="button" class="retag" data-retag>Retag as dross</button>'
                '<button type="button" class="retag" data-insert-below>+ Insert step below</button></div>'
                '<textarea class="notes" data-notes placeholder="Add a note for this step…">'
                f"{notes}</textarea></div>"
                f'<div class="thumbs">{thumbs}</div>'
                "</div>"
            )
        else:
            intent = f'<div class="intent">{_esc(st.intent)}</div>' if st.intent else ""
            desc = f'<div class="intent">{_esc(st.description)}</div>' if st.description else ""
            steps.append(
                '<div class="step">'
                f'<div class="num">{st.index + 1}</div>'
                f'<div class="body"><h3>{title}</h3>{meta}{desc}{intent}</div>'
                f'<div class="thumbs">{thumbs}</div>'
                "</div>"
            )

    if interactive:
        lede = (
            "ProCap drafted one ordered step per kept (golden) moment, timed from the "
            "recording. Retag any step it misjudged, add a note to each, and add off-screen "
            "steps for work the recording could not capture — at the end, or below any step "
            "with <b>Insert step below</b> — then save the result as a PDF."
        )
        toolbar = (
            '<div class="toolbar no-print">'
            '<button type="button" class="btn" data-addblank>+ Add off-screen step at end</button>'
            '<button type="button" class="btn primary" data-print>Save as PDF</button></div>'
        )
        title_block = (
            '<div class="printtitle">Standard operating procedure</div>'
            f'<div class="printmeta">Drafted by ProCap from {_esc(rv.meta.get("source_video", rv.name))}</div>'
        )
        return (
            '<section class="stage" id="stage-procedure" data-editor>'
            '<h2>Draft procedure</h2>'
            f'<p class="lede no-print">{lede}</p>{toolbar}{title_block}'
            f'{"".join(steps)}{_BLANK_STEP_TEMPLATE}</section>'
        )

    lede = (
        "One ordered step per kept keyframe, with a time estimate carried from the segment "
        "span. A stretch held longer than the active-action threshold is reported as "
        "<span class='held'>≤X active + Y held (attribution unknown)</span> — pixels alone "
        "can't tell active work from waiting."
    )
    return (
        '<section class="stage" id="stage-procedure"><h2>Procedure '
        f'— {_esc(rv.procedure.title)}</h2>'
        f'<p class="lede">{lede}</p>{"".join(steps)}</section>'
    )


# Grouped finding presentation: outside-in label + a tooltip explaining the kind. The order
# here is the order the groups render in.
_FINDING_GROUPS = [
    (FindingKind.MISSING_STEP.value, "Missing",
     "A step the recording shows that your written SOP never mentions."),
    (FindingKind.OUT_OF_ORDER.value, "Out of order",
     "A step your SOP documents in a different sequence than the recording."),
    (FindingKind.EXTRA_IN_DOC.value, "Not in the recording",
     "A step your SOP describes that the recording never shows happening."),
    (FindingKind.UNDER_DOCUMENTED.value, "Thinly covered",
     "A step your SOP mentions, but only sketchily given what the recording shows."),
]

# kind -> short label, for tagging flagged steps in the rendered reference SOP.
_FINDING_LABELS = {kind: label for kind, label, _ in _FINDING_GROUPS}


def _step_first_keyframe(rv: RunView, step_index_0: int) -> Keyframe | None:
    """The first available keyframe a 0-based procedure step was built from — the screencap
    that shows what the recording actually did at that step."""
    if rv.procedure is None or not (0 <= step_index_0 < len(rv.procedure.steps)):
        return None
    kbi = rv.keyframe_by_index
    for ki in rv.procedure.steps[step_index_0].keyframe_indexes:
        if ki in kbi:
            return kbi[ki]
    return None


def _finding_row(rv: RunView, f, verdict_html: str) -> str:
    """One finding rendered as a side-by-side: what the SOP says vs. what the recording
    showed (with the actual screencap), then the rationale. This is the actionable form —
    an author can see the divergence, not just be told a step number."""
    # Left column: the written-doc side.
    if f.doc_ref:
        doc_body = _esc(f.doc_ref)
    else:
        doc_body = '<span class="nofrm">— no matching step in your SOP —</span>'
    doc_col = f'<div class="fcol"><div class="hdr">Your SOP says</div>{doc_body}</div>'

    # Right column: the recording side, with the screencap when the finding maps to a step.
    if f.procedure_step_index is not None:
        st = (rv.procedure.steps[f.procedure_step_index]
              if rv.procedure and f.procedure_step_index < len(rv.procedure.steps) else None)
        kf = _step_first_keyframe(rv, f.procedure_step_index)
        img = ""
        if kf is not None:
            img = (f'<a href="#kf-{rv.name}-{kf.index}" data-lb="kf-{rv.name}-{kf.index}">'
                   f'<img src="{_img_src(rv.name, kf)}" alt="recording at step '
                   f'{f.procedure_step_index + 1}"></a>')
        cap = ""
        if st is not None:
            cap = f"<div><b>Step {st.index + 1}: {_esc(st.title)}</b>"
            if st.description:
                cap += f'<br><span class="muted">{_esc(st.description)}</span>'
            cap += "</div>"
        rec_col = (f'<div class="fcol"><div class="hdr">Recording showed</div>'
                   f'<div class="recbody">{img}{cap}</div></div>')
    else:
        rec_col = ('<div class="fcol"><div class="hdr">Recording showed</div>'
                   '<span class="nofrm">never seen in the recording</span></div>')

    return (
        '<div class="finding" data-finding>'
        f'<div class="fcols">{doc_col}{rec_col}</div>'
        f'<div class="why">{_esc(f.detail)}</div>{verdict_html}</div>'
    )


def _render_reference_sop(rv: RunView) -> str:
    """Render the SOP being audited against, with flagged steps highlighted. The reference
    document was never shown before — without it a divergence can't be judged."""
    text = rv.written_doc_text()
    if not text:
        return ""
    doc_steps = parse_written_steps(text)
    if not doc_steps:
        return ""
    # Which doc steps a finding touches (findings carry the doc step text in doc_ref).
    flagged: dict[str, str] = {}
    for f, label in ((f, _FINDING_LABELS.get(f.kind, "flagged")) for f in rv.audit.findings):
        if f.doc_ref:
            flagged[f.doc_ref] = label
    items = []
    for t in doc_steps:
        tag = flagged.get(t)
        cls = ' class="flag"' if tag else ""
        tag_html = f'<span class="ftag">{_esc(tag)}</span>' if tag else ""
        items.append(f"<li{cls}>{_esc(t)}{tag_html}</li>")
    return (
        '<div class="sopdoc"><h3>The SOP being checked</h3>'
        f'<ol>{"".join(items)}</ol></div>'
    )


def _render_audit(rv: RunView) -> str:
    if rv.audit is None:
        return ""  # audit is optional; omit the section entirely when absent
    findings = rv.audit.findings
    by_kind: dict[str, list] = {}
    for f in findings:
        by_kind.setdefault(f.kind, []).append(f)

    groups_html = []
    for kind, label, tip in _FINDING_GROUPS:
        items = by_kind.get(kind, [])
        if not items:
            continue
        verdict = (
            '<div class="verdict">'
            '<button type="button" class="vbtn on-confirm no-print" data-confirm>Confirm gap</button>'
            '<button type="button" class="vbtn on-deny no-print" data-deny>Not a gap</button>'
            '<span class="vstate" data-vstate>Unreviewed</span></div>'
        )
        rows = "".join(_finding_row(rv, f, verdict) for f in items)
        groups_html.append(
            f'<h3 class="findgroup" title="{tip}">{_esc(label)} '
            f'<span class="muted">({len(items)})</span></h3>{rows}'
        )
    if groups_html:
        fin = "".join(groups_html)
    else:
        fin = '<p class="empty">No gaps found — your written doc covers every step the recording shows.</p>'

    sop_panel = _render_reference_sop(rv)

    # The recording is the reference; the written doc is what's being checked against it.
    ref_line = (
        '<p class="lede no-print">The recording is treated as the reference. ProCap matched '
        "each step it captured against the provided SOP and flagged where they diverge — each "
        "flag shows the SOP wording beside the actual screencap. Confirm or dismiss each, then "
        "save the reviewed result as a PDF.</p>"
    )

    # Plain count, never a grade-colored percentage. covered = generated steps the doc covers.
    n_missing = len(by_kind.get(FindingKind.MISSING_STEP.value, []))
    m_steps = len(rv.procedure.steps) if rv.procedure is not None else None
    if m_steps is not None:
        covered = max(0, m_steps - n_missing)
        gap = (
            f'<p class="auditcount">Your SOP covers <b>{covered} of {m_steps}</b> steps the '
            "recording shows.</p>"
        )
    else:
        gap = ""

    # Honesty: how the audit aligned steps bounds what it can find. Label it by method
    # (audit.py records AuditReport.method) — never present a count ratio as a content audit.
    doc = _esc(rv.audit.written_doc)
    method = rv.audit.method
    if method == AuditMethod.VLM.value:
        method_line = (
            f"<b>VLM content match</b> against <code>{doc}</code>: each generated step matched "
            "to the doc step describing the same action by meaning."
        )
    elif method == AuditMethod.LEXICAL.value:
        floor = rv.meta.get("match_floor")
        floor_txt = f" (match floor {floor})" if floor is not None else ""
        method_line = (
            f"<b>Offline lexical content match</b> against <code>{doc}</code>{floor_txt} — "
            "<b>no model</b>: each step matched to the doc step with the highest word overlap, "
            "then order checked. It is <b>lexical, not semantic</b> — it can mis-pair on shared "
            "vocabulary; <em>thinly covered</em> (a thinness judgement) is left to the VLM."
        )
    else:  # count
        placeholder_titles = bool(
            rv.procedure and any("[fill in" in s.title for s in rv.procedure.steps)
        )
        method_line = (
            f"<b>Structural check only</b> (no content): generated steps aligned to "
            f"<code>{doc}</code> <b>by position and count</b>. Out-of-order / thinly-covered "
            "detection is <b>content-dependent</b> — it needs step titles/intents (from the "
            "VLM <em>or</em> a manual fill-in)."
            + (
                " Generated titles are still <span class='todo'>[fill in]</span> placeholders "
                "here, so there is nothing to match by content yet."
                if placeholder_titles
                else ""
            )
        )
    # Honesty on the order check: it is a greedy monotonic comparison, not full alignment.
    order_caveat = ""
    if by_kind.get(FindingKind.OUT_OF_ORDER.value):
        order_caveat = (
            '<p class="no-print" style="margin:6px 0 0;font-size:12px;color:var(--muted)">'
            "<b>On ordering:</b> each captured step is matched to its best SOP step, then flagged "
            "if that match falls earlier than one already seen — a greedy left-to-right check, "
            "not full sequence alignment. When two steps are swapped it flags the later one, "
            "which may not be the step actually misplaced. Treat it as a pointer to re-read, not "
            "a verdict on which step moved.</p>"
        )
    method_html = (
        f'<p class="no-print" style="margin:8px 0 0;font-size:12px;color:var(--muted)">{method_line}</p>'
    )
    toolbar = (
        '<div class="toolbar no-print" style="margin-top:18px">'
        '<button type="button" class="btn primary" data-print>Save as PDF</button></div>'
    )
    title_block = (
        '<div class="printtitle">SOP conformance review</div>'
        f'<div class="printmeta">{_esc(rv.audit.written_doc)} checked against '
        f'{_esc(rv.meta.get("source_video", rv.name))}</div>'
    )
    return (
        '<section class="stage" id="stage-audit" data-review><h2>'
        "Conformance against the provided SOP</h2>"
        f"{ref_line}{title_block}{gap}{sop_panel}{fin}{order_caveat}{method_html}"
        f"{toolbar}</section>"
    )


def _knob_html(rv: RunView, items: list[tuple[str, str]]) -> str:
    """Render 'name = current-value' chips for the named meta keys, reading live values from
    meta.json. A key absent from meta (e.g. the stage hasn't run) shows '—'."""
    chips = []
    for key, human in items:
        if key in rv.meta:
            val = rv.meta[key]
            chips.append(f'<code title="{_esc(human)}">{_esc(key)} = {_esc(val)}</code>')
        else:
            chips.append(f'<code title="{_esc(human)} (not recorded for this run)">{_esc(key)} = —</code>')
    return " · ".join(chips)


def _worked_trace(rv: RunView) -> str:
    """A concrete trace through THIS run's real artifacts — no hardcoded numbers."""
    parts = [f"{_fmt_t(rv.duration)} recording"]
    fps = rv.meta.get("fps_sampled")
    if fps is not None:
        parts.append(f"sampled at {fps} fps")
    parts.append(f"{len(rv.keyframes)} keyframes")
    if rv.segments is not None:
        n_drop = sum(1 for s in rv.segments if s.kind == SegmentKind.DROSS)
        drop_txt = f" ({n_drop} dropped: reverted/idle detour)" if n_drop else " (none dropped)"
        parts.append(f"{len(rv.segments)} segments{drop_txt}")
    if rv.procedure is not None:
        parts.append(
            f"{len(rv.procedure.steps)} steps ({_fmt_t(rv.procedure.total_est_seconds)})"
        )
    return ", ".join(parts)


def _render_how(rv: RunView) -> str:
    """Plain-language 'how it works' — each stage's I/O, mechanism, live knobs, and one honest
    limit, anchored to the live section so the explanation connects to seeing it work."""
    stages = [
        {
            "n": 1, "anchor": "stage-keyframes", "title": "Decompose",
            "io": "recording (video) → keyframes",
            "mech": "ffmpeg samples frames at a fixed rate; each is diffed against the current "
                    "stable frame by perceptual-hash / SSIM, and a new keyframe opens only when "
                    "the change clears a threshold AND the new state is held past a dwell.",
            "knobs": [("change_threshold", "minimum changed-pixel fraction to count as a new state"),
                      ("min_dwell_s", "minimum seconds a state must hold to survive as a keyframe")],
            "limit": "It keys on visual change, so a change too subtle to clear the threshold — "
                     "or one that never settles — is missed.",
        },
        {
            "n": 2, "anchor": "stage-golden", "title": "Keep or drop",
            "io": "keyframes → golden / dross segments",
            "mech": "revert-detection — a later keyframe returning to an earlier state marks the "
                    "excursion between them as an abandoned detour (dross) — plus the dwell "
                    "check; everything else is kept as golden. Confidence comes from each "
                    "call's own margin.",
            "knobs": [("min_dwell_s", "a state held briefer than this is treated as a transient")],
            "limit": "Only dross that REVERTS is caught; non-reverting dross — idle time, "
                     "mouse-wander that doesn't return — leaks through (visible in the accuracy panel).",
        },
        {
            "n": 3, "anchor": "stage-procedure", "title": "Write timed steps",
            "io": "golden segments → ordered, time-estimated procedure",
            "mech": "one step per kept keyframe, in time order; each step's estimate is its "
                    "segment span, with any hold beyond the active-action threshold split out "
                    "as 'held' rather than counted as work.",
            "knobs": [("max_active_s", "span beyond this is reported as held (attribution unknown), not active")],
            "limit": "Pixels can't separate active work from waiting, and offline the step "
                     "titles are placeholders you fill in (or a VLM drafts).",
        },
        {
            "n": 4, "anchor": "stage-audit", "title": "Audit (optional)",
            "io": "procedure + your written doc → gap findings",
            "mech": "each generated step is matched to a doc step — offline by word overlap "
                    "(lexical) or, with a key, by meaning (VLM) — then order and coverage are "
                    "checked. The recording is the reference; the doc is graded against it.",
            "knobs": [("match_floor", "minimum word-overlap for a lexical step match to count")],
            "limit": "Offline the match is lexical, not semantic — it can mis-pair on shared "
                     "vocabulary, and 'thinly covered' needs the VLM.",
        },
    ]
    cards = []
    for s in stages:
        knobs = _knob_html(rv, s["knobs"])
        cards.append(
            '<div class="howstage">'
            f'<h3><span class="n">{s["n"]}</span> {s["title"]} '
            f'<a class="howlink" href="#{s["anchor"]}">see it live ↓</a></h3>'
            f'<p class="howio"><b>{s["io"]}</b></p>'
            f'<p>{s["mech"]}</p>'
            f'<p class="howknob">Knob(s): {knobs}</p>'
            f'<p class="howlimit"><b>Limit:</b> {s["limit"]}</p>'
            "</div>"
        )
    trace = _worked_trace(rv)
    return (
        '<details class="disc"><summary>How it works</summary>'
        '<p class="lede">Four stages, all running offline by default; a vision-model key only '
        "enriches step wording and the audit. Knob values below are read live from this run's "
        "<code>meta.json</code>.</p>"
        f'<p class="howtrace"><b>This run, end to end:</b> {_esc(trace)}.</p>'
        f'{"".join(cards)}</details>'
    )


def _render_faq(rv: "RunView | None") -> str:
    """FAQ in the user's questions — not implementation trivia. The honesty caveats live here as
    the answer to 'what's it bad at'."""
    acc = ""
    if rv is not None and rv.segments and rv.labels:
        sc = score_against_labels(rv.segments, rv.labels)
        acc = (
            f" On the labeled example it scores precision {sc.get('precision',0):.2f}, recall "
            f"{sc.get('recall',0):.2f}, F1 {sc.get('f1',0):.2f} — but that's measured on the one "
            "clip any thresholds were tuned against (in-sample), so treat it as a sanity check, "
            "not a guarantee."
        )
    qa = [
        ("What videos does this work on?",
         "Screen recordings of someone operating a technical GUI — a machine, a lab instrument, "
         "a control panel. It looks for durable on-screen state changes, so steady GUI work "
         "suits it; fast video/animation does not."),
        ("Do I need an API key?",
         "No. The whole pipeline — keyframes, keep/drop, timed steps, and a structural audit — "
         "runs offline with heuristics. A vision-model key only adds auto-written step titles "
         "and a semantic (content) audit; nothing breaks without one."),
        ("How accurate is the keep/drop call?",
         "It's measured against ground truth, shown in the accuracy panel in the note-taking "
         "demo's evidence section, not asserted." + acc),
        ("When will the steps be wrong / what's it bad at?",
         "Three honest limits, all surfaced in the page: (1) idle or dead time that never "
         "reverts isn't detected yet, so a step's time can include waiting — shown as “held” "
         "rather than hidden; (2) offline, step titles are drafts you fill in; (3) accuracy is "
         "from one labeled clip (in-sample). It trims fumbles well; it does not understand "
         "intent without a vision model."),
        ("How do I actually use it?",
         "Record your screen, run <code>procap run &lt;video&gt;</code>, review the draft on a "
         "page like this, and export the procedure as Markdown."),
    ]
    items = "".join(f"<dt>{q}</dt><dd>{a}</dd>" for q, a in qa)
    return (
        '<details class="disc"><summary>FAQ — would this help me?</summary>'
        f'<dl class="faq">{items}</dl></details>'
    )


def _default_active(runs: list[Path]) -> Path | None:
    """Land on Demo A (conformance) — the first card — so the page opens on what's labelled
    A. Falls back to the first run if no conformance run is present."""
    for p in runs:
        if RunView(p).mode == "conformance":
            return p
    return runs[0] if runs else None


def _demo_label(rv: RunView) -> tuple[str, str, str]:
    """(badge, name, one-line purpose) for the run, by its structural mode. The two canned
    runs carry 'Demo A' (conformance, the first card) / 'Demo B' (note-taking) so the pair
    reads A → B; a user's uploaded run reads 'Your run' so it doesn't duplicate the letters."""
    mine = rv.name.startswith("upload_")
    if rv.mode == "conformance":
        return ("Your run" if mine else "Demo A",
                "Conformance review" if mine else "Qualify against an SOP",
                "Your recording vs your SOP" if mine else "Recording vs a provided SOP")
    return ("Your run" if mine else "Demo B",
            "Drafted procedure" if mine else "Document a procedure",
            "Your recording → a written SOP" if mine else "Recording → a written SOP")


def _render_demo_chooser(runs: list[Path], active: Path | None) -> str:
    """Two cards, one per run, each naming its demo and purpose (not the raw run dir name)."""
    cards = []
    for p in runs:
        badge, name, sub = _demo_label(RunView(p))
        on = " active" if active and p == active else ""
        cards.append(
            f'<a class="card{on}" href="/?run={quote(p.name)}">'
            f'<span class="dlabel">{_esc(badge)}</span>'
            f'<span class="dname">{_esc(name)}</span>'
            f'<span class="dsub">{_esc(sub)}</span></a>'
        )
    return f'<div class="chooser">{"".join(cards)}</div>'


def _render_demo_intro(rv: RunView) -> str:
    """The purpose of THIS demo and what its controls do — distinct per mode."""
    badge, name, _sub = _demo_label(rv)
    chips = []
    proof = ""  # honesty line, promoted onto the first screenful of the tab that owns it
    if rv.mode == "conformance":
        purpose = (
            "You are performing a <b>provided</b> SOP to validate it. ProCap matches your "
            "recording against that SOP and reports where they diverge — steps it shows "
            "that the SOP omits, steps documented out of order, and steps the SOP describes "
            "but the recording never performs. Confirm or dismiss each flag, then save the "
            "reviewed result as a PDF."
        )
        if rv.procedure and rv.procedure.steps and rv.audit:
            n = len(rv.procedure.steps)
            chips.append(f"<span><b>{rv.audit.coverage*100:.0f}%</b> of steps matched</span>")
            chips.append(f"<span><b>{len(rv.audit.findings)}</b> divergences flagged</span>")
            chips.append(f"<span><b>{n}</b> steps captured</span>")
    else:
        purpose = (
            "You have a recording of a task you already do and want to write up. ProCap "
            "guesses which stretches are <b>golden</b> and which are <b>dross</b>, drafts one "
            "timed step per kept moment, and lets you retag a wrong guess, add a note to each "
            "step, and add off-screen steps the recording could not capture. The result is a "
            "written SOP you can save as a PDF."
        )
        if rv.segments is not None and rv.procedure is not None:
            n_dross = sum(1 for s in rv.segments if s.kind == SegmentKind.DROSS)
            chips.append(f"<span><b>{len(rv.procedure.steps)}</b> steps drafted</span>")
            chips.append(f"<span><b>{n_dross}</b> stretch{'es' if n_dross != 1 else ''} dropped</span>")
            chips.append(f"<span><b>{_fmt_t(rv.duration)}</b> of recording</span>")
        # The keep/drop call's honesty proof, lifted out of the collapsed evidence panel so it
        # lands on this tab's first screenful (the full panel still renders below in evidence).
        if rv.segments is not None and rv.labels:
            f1 = score_against_labels(rv.segments, rv.labels).get("f1", 0.0)
            proof = (
                f'<p class="proof">Measured, not asserted — the keep/drop call scores '
                f'<b>F1 {f1:.2f}</b> on a labeled clip (in-sample). Full accuracy panel in '
                "this demo's evidence section below.</p>"
            )
    chip_html = f'<div class="steps-of">{"".join(chips)}</div>' if chips else ""
    return (
        '<section class="demointro">'
        f'<h2>{_esc(badge)} · {_esc(name)}</h2>'
        f'<p class="purpose">{purpose}</p>{chip_html}{proof}</section>'
    )


def _render_about(rv: RunView) -> str:
    """One product-level block: what ProCap is, the two domain terms (a one-liner each), and
    the deeper 'how it works' / FAQ as nested collapsibles. Always-true, shared by both demos."""
    why = (
        "ProCap turns a screen recording of a technical GUI — operating a machine, a lab "
        "instrument, a control panel — into a written, time-estimated procedure, and can "
        "check that procedure against an existing SOP."
    )
    gloss = (
        '<div class="gloss">'
        '<span><b><i style="background:var(--golden)"></i>Golden</b> — a consequential action, '
        "kept as a step.</span>"
        '<span><b><i style="background:var(--dross)"></i>Dross</b> — a mis-click, mouse wander, '
        "or dead time, dropped.</span>"
        "</div>"
    )
    return (
        '<section class="about"><h2>What ProCap does</h2>'
        f'<p class="why">{why}</p>{gloss}'
        f"{_render_how(rv)}{_render_faq(rv)}</section>"
    )


def _render_upload() -> str:
    """The 'run your own' panel: upload a recording (+ optional SOP) and ProCap runs the full
    pipeline on it server-side, then redirects to the result. This is what makes Save-as-PDF a
    real artifact rather than a copy of a canned demo."""
    return (
        '<section class="upload no-print"><h2>Run your own recording</h2>'
        '<p class="lede">Upload a screen recording of a GUI task — ProCap runs the full '
        "pipeline on it (keyframes → golden/dross → a timed draft procedure). Add an existing "
        "SOP and it returns a conformance review against that doc instead. Everything runs "
        "locally; nothing leaves this machine.</p>"
        '<form method="post" action="/run" enctype="multipart/form-data" data-uploadform>'
        '<div class="row">'
        '<div class="fld"><label for="up-video">Recording (required)</label>'
        '<input id="up-video" type="file" name="video" accept="video/*" required>'
        '<p class="hint">mp4 / mov / webm / mkv, up to 50&nbsp;MB. A short clip processes fastest.</p>'
        "</div>"
        '<div class="fld"><label for="up-sop">Existing SOP (optional)</label>'
        '<textarea id="up-sop" name="sop" placeholder="Paste a numbered written procedure to '
        'audit the recording against it. Leave blank to just draft a new one."></textarea>'
        '<p class="hint">One step per line, or a numbered list.</p></div>'
        "</div>"
        '<div class="actions"><button type="submit" class="btn primary">Run ProCap</button></div>'
        "</form></section>"
    )


def _render_demo_block(rv: RunView) -> str:
    """One card for the selected demo: the intro header, then the output (the point) leading,
    then an 'evidence' collapsible holding the upstream pipeline (how ProCap got there)."""
    intro = _render_demo_intro(rv)
    if rv.mode == "conformance":
        primary = _render_audit(rv)
        supporting = (
            _render_procedure(rv, interactive=False)
            + _render_keyframes(rv)
            + _render_segments(rv)
        )
    else:
        primary = _render_procedure(rv, interactive=True)
        supporting = _render_keyframes(rv) + _render_segments(rv) + _render_eval(rv)
    evidence = (
        '<details class="disc no-print"><summary>How ProCap produced this</summary>'
        f"{supporting}</details>"
    )
    return f'<section class="demoblock">{intro}{primary}{evidence}</section>'


def render_page(runs: list[Path], active: Path | None) -> str:
    if active is None:
        about = chooser = demo = lightboxes = ""
        sub = ""
    else:
        rv = RunView(active)
        if rv.vlm_active:
            badge = '<span class="badge vlm-on">VLM: on</span>'
        else:
            badge = (
                '<span class="badge vlm-off">VLM: off — heuristic-only floor</span>'
                '<span class="badge-note">titles &amp; semantic audit appear when an API key is set</span>'
            )
        sub = (
            f'<div class="tag">{_esc(rv.meta.get("source_video", rv.name))} · '
            f'sampled @ {rv.meta.get("fps_sampled", "?")} fps &nbsp; {badge}</div>'
        )
        about = _render_about(rv)
        chooser = _render_demo_chooser(runs, active)
        meta_line = f'<header class="no-print metaline">{sub}</header>'
        demo = meta_line + _render_demo_block(rv)
        # Lightbox overlays live at page level so the keyframe filmstrip (now inside a
        # collapsed evidence <details>) and the procedure thumbnails can both open them.
        lightboxes = _keyframe_lightboxes(rv)
    empty = ('<p class="empty">No runs yet — upload a recording above, or generate one with '
             "<code>procap run &lt;video&gt;</code>.</p>")
    upload = _render_upload()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ProCap demo{(' · ' + _esc(active.name)) if active else ''}</title>
<style>{CSS}</style></head>
<body>
<header class="top no-print"><h1>ProCap</h1>
<div class="tag">turn a screen recording of a GUI task into a written procedure — or check one against an existing SOP</div></header>
<div id="proc-overlay" aria-hidden="true"><div class="spin"></div>
<div>Running ProCap on your recording…<div class="sub">extracting keyframes, classifying golden/dross, drafting — this can take a moment</div></div></div>
<div class="wrap">
{about if runs else ''}
{upload}
{(chooser + demo) if runs else empty}
{lightboxes}
<footer class="no-print">ProCap demo · runs entirely offline · artifacts under runs/</footer>
</div>
<script>
/* Progressive enhancement. The page is readable with JS off; this layer adds scroll
   preservation, in-place lightboxes, the note-taking / conformance editors (in-memory
   only — edits are not persisted), and print-to-PDF. No dependencies. */
(function () {{
  try {{
    var SK = "procap-scroll";
    var y = sessionStorage.getItem(SK);
    if (y !== null) window.scrollTo(0, parseInt(y, 10));
    window.addEventListener("beforeunload", function () {{
      sessionStorage.setItem(SK, String(window.scrollY));
    }});
  }} catch (e) {{}}

  function retag(step) {{
    if (!step) return;
    var next = step.getAttribute("data-kind") === "dross" ? "golden" : "dross";
    step.setAttribute("data-kind", next);
    step.classList.toggle("golden", next === "golden");
    step.classList.toggle("dross", next === "dross");
    var s = step.querySelector(".tag-state");
    if (s) s.textContent = next === "golden" ? "golden — kept" : "dross — dropped";
    var b = step.querySelector("[data-retag]");
    if (b) b.textContent = next === "golden" ? "Retag as dross" : "Retag as golden";
  }}
  function newBlankStep() {{
    var tpl = document.getElementById("blankstep-tpl");
    return tpl ? tpl.content.firstElementChild.cloneNode(true) : null;
  }}
  function addBlank(btn) {{  // toolbar: append at the end (before the <template>)
    var tpl = document.getElementById("blankstep-tpl");
    var editor = btn.closest("[data-editor]");
    var node = newBlankStep();
    if (!tpl || !editor || !node) return;
    editor.insertBefore(node, tpl);
  }}
  function insertBelow(step) {{  // per-step: insert directly after the chosen step
    var node = newBlankStep();
    if (!step || !node || !step.parentNode) return;
    step.parentNode.insertBefore(node, step.nextSibling);
  }}
  function verdict(f, state) {{
    if (!f) return;
    var same = f.getAttribute("data-verdict") === state;
    var now = same ? "" : state;
    if (now) f.setAttribute("data-verdict", now); else f.removeAttribute("data-verdict");
    f.classList.toggle("confirmed", now === "confirmed");
    f.classList.toggle("denied", now === "denied");
    var cb = f.querySelector("[data-confirm]"), db = f.querySelector("[data-deny]");
    if (cb) cb.classList.toggle("active", now === "confirmed");
    if (db) db.classList.toggle("active", now === "denied");
    var v = f.querySelector("[data-vstate]");
    if (v) v.textContent = now === "confirmed" ? "Confirmed gap"
                          : now === "denied" ? "Dismissed" : "Unreviewed";
  }}

  document.addEventListener("click", function (ev) {{
    var opener = ev.target.closest("a[data-lb]");
    if (opener) {{
      var box = document.getElementById(opener.getAttribute("data-lb"));
      if (box) {{ ev.preventDefault(); box.classList.add("show"); }}
      return;
    }}
    if (ev.target.classList.contains("lightbox") || ev.target.closest("[data-lbclose]")) {{
      ev.preventDefault();
      var open = document.querySelector(".lightbox.show");
      if (open) open.classList.remove("show");
      return;
    }}
    if (ev.target.closest("[data-retag]")) return retag(ev.target.closest("[data-step]"));
    if (ev.target.closest("[data-remove]")) {{
      var s = ev.target.closest("[data-step]"); if (s) s.remove(); return;
    }}
    if (ev.target.closest("[data-addblank]")) return addBlank(ev.target.closest("[data-addblank]"));
    if (ev.target.closest("[data-insert-below]")) return insertBelow(ev.target.closest("[data-step]"));
    if (ev.target.closest("[data-print]")) return window.print();
    if (ev.target.closest("[data-confirm]")) return verdict(ev.target.closest("[data-finding]"), "confirmed");
    if (ev.target.closest("[data-deny]")) return verdict(ev.target.closest("[data-finding]"), "denied");
  }});
  document.addEventListener("keydown", function (ev) {{
    if (ev.key === "Escape") {{
      var open = document.querySelector(".lightbox.show");
      if (open) open.classList.remove("show");
    }}
  }});

  // Upload: client-side size guard, then show the processing overlay (the POST is synchronous).
  var MAX_BYTES = {MAX_UPLOAD_BYTES};
  document.addEventListener("submit", function (ev) {{
    var form = ev.target.closest("[data-uploadform]");
    if (!form) return;
    var fi = form.querySelector('input[type=file]');
    if (fi && fi.files && fi.files[0] && fi.files[0].size > MAX_BYTES) {{
      ev.preventDefault();
      alert("That recording is larger than the 50 MB limit. Trim it or use a shorter clip.");
      return;
    }}
    var ov = document.getElementById("proc-overlay");
    if (ov) ov.classList.add("show");
  }});
}})();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Upload handling (POST /run): user recording -> full pipeline -> new run dir
# ---------------------------------------------------------------------------


def _parse_multipart(body: bytes, boundary: bytes) -> dict[str, dict]:
    """Minimal multipart/form-data parser, stdlib-only (the `cgi` module is deprecated and
    removed in 3.13). Returns {field_name: {"filename": str|None, "data": bytes}}. Scoped to
    this demo's one-file + one-text-field form — not an RFC-complete implementation.

    Framing: parts are separated by `--boundary`; each part is `\\r\\n`<headers>`\\r\\n\\r\\n`<data>
    with a trailing `\\r\\n` before the next separator. Exactly one leading and one trailing
    `\\r\\n` are stripped so binary payloads ending in CR/LF survive intact."""
    out: dict[str, dict] = {}
    for part in body.split(b"--" + boundary):
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part or part == b"--":
            continue
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        name = filename = None
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-disposition:"):
                m = _DISPOSITION_RE.search(line)
                if m:
                    name = m.group(1).decode("utf-8", "replace")
                    if m.group(2) is not None:
                        filename = m.group(2).decode("utf-8", "replace")
        if name is not None:
            out[name] = {"filename": filename, "data": data}
    return out


def _prune_uploads(base: Path, keep: int = MAX_UPLOADS_KEPT) -> None:
    """Keep only the most recent `keep` upload_* runs (names are timestamp-sortable)."""
    uploads = sorted((p for p in base.glob("upload_*") if p.is_dir()), reverse=True)
    for stale in uploads[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def process_upload(base: Path, filename: str, video_bytes: bytes, sop_text: str) -> str:
    """Persist an uploaded recording (+ optional SOP), run the full pipeline into a fresh
    `upload_<ts>` run dir, and return its name. Raises on pipeline failure (caller reports)."""
    from . import cli  # lazy: cli imports webdemo in cmd_serve; avoid an import cycle at load

    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    name, i = f"upload_{ts}", 1
    while (base / name).exists():
        name, i = f"upload_{ts}_{i}", i + 1
    run = Run(base / name).ensure()

    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_VIDEO_EXTS:
        ext = ".mp4"
    video_path = run.dir / f"source{ext}"
    video_path.write_bytes(video_bytes)

    against = None
    if sop_text.strip():
        against = run.dir / "written_procedure.md"
        against.write_text(sop_text)

    cli.run_pipeline(video_path, against=against, run=run)
    _prune_uploads(base)
    return name


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def make_handler(base: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quieter console
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error_page(self, code: int, msg: str):
            body = (
                "<!doctype html><meta charset='utf-8'>"
                "<title>ProCap — upload error</title>"
                "<body style='font:15px sans-serif;max-width:640px;margin:60px auto;padding:0 20px'>"
                f"<h2>Couldn't process that recording</h2><p>{_esc(msg)}</p>"
                "<p><a href='/'>← back to the demo</a></p></body>"
            )
            self._send(code, body.encode("utf-8"), "text/html; charset=utf-8")

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path == "/img":
                return self._serve_img(qs)
            if parsed.path in ("/", "/index.html"):
                return self._serve_index(qs)
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/run":
                return self._send(404, b"not found", "text/plain")
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                return self._error_page(400, "Expected a multipart form upload.")
            m = re.search(r"boundary=([^;]+)", ctype)
            if not m:
                return self._error_page(400, "Malformed upload (no multipart boundary).")
            boundary = m.group(1).strip().strip('"').encode("utf-8")
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length <= 0:
                return self._error_page(400, "Empty upload.")
            if length > MAX_UPLOAD_BYTES:
                mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                return self._error_page(413, f"That recording exceeds the {mb} MB limit.")
            body = self.rfile.read(length)
            fields = _parse_multipart(body, boundary)
            video = fields.get("video")
            if not video or not video.get("data") or not video.get("filename"):
                return self._error_page(400, "No video file found in the upload.")
            sop = fields.get("sop")
            sop_text = sop["data"].decode("utf-8", "replace") if sop else ""
            try:
                name = process_upload(base, video["filename"], video["data"], sop_text)
            except Exception as exc:  # ffmpeg missing, bad video, etc. — degrade to a message
                return self._error_page(
                    500, f"The pipeline failed on that recording: {exc}")
            self.send_response(303)
            self.send_header("Location", f"/?run={quote(name)}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _serve_index(self, qs):
            runs = _discover_runs(base)
            active = None
            want = qs.get("run", [None])[0]
            if want:
                for p in runs:
                    if p.name == want:
                        active = p
                        break
            elif runs:
                active = _default_active(runs)
            html_doc = render_page(runs, active)
            self._send(200, html_doc.encode("utf-8"), "text/html; charset=utf-8")

        def _serve_img(self, qs):
            run = qs.get("run", [None])[0]
            file = qs.get("file", [None])[0]
            if not run or not file or "/" in file or "\\" in file or ".." in (run, file):
                return self._send(400, b"bad request", "text/plain")
            img = base / run / "keyframes" / file
            if not img.exists():
                return self._send(404, b"no image", "text/plain")
            self._send(200, img.read_bytes(), "image/png")

    return Handler


def serve(base: str | Path = "runs", host: str = "127.0.0.1", port: int = 8000):
    base = Path(base)
    httpd = ThreadingHTTPServer((host, port), make_handler(base))
    runs = _discover_runs(base)
    print(f"procap demo serving {len(runs)} run(s) from {base}/ at http://{host}:{port}")
    for p in runs:
        print(f"  · {p.name}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Local web demo of procap run artifacts.")
    ap.add_argument("--runs", default="runs", help="base dir holding run dirs (default runs/)")
    ap.add_argument("--run", default=None, help="serve a single run dir (its parent becomes the base)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    base = Path(args.run).parent if args.run else Path(args.runs)
    serve(base, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
