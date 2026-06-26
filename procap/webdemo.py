"""A dependency-free local web demo of the procap pipeline.

Reads the on-disk artifacts a run produces (see procap.run) and renders them as a
single page that walks the four stages in order: keyframes -> golden/dross
segments -> synthesized procedure -> audit. The point is to make the pipeline's
*judgement* legible — which stretches it kept and why, what it dropped, how it
timed the result — not just to dump JSON.

No web framework: this is the stdlib http.server so the demo carries zero new
dependency (procap's heuristics are the always-on baseline; the demo should be too).

Run it:
    python -m procap.webdemo                 # serve every run under runs/
    python -m procap.webdemo --run runs/foo  # serve one run
    procap serve                             # same, via the CLI
"""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

from .model import Procedure, Segment, Keyframe, AuditReport, SegmentKind
from .eval import score_against_labels

EVAL_STEP = 0.1  # time-grid resolution the scorer uses; reused to convert grid points -> seconds

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
    def keyframe_by_index(self) -> dict[int, Keyframe]:
        return {k.index: k for k in self.keyframes}

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
.runbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
.runbar a { padding: 5px 12px; border: 1px solid var(--line); border-radius: 999px; background: #fff; font-size: 13px; }
.runbar a.active { background: var(--ink); color: #fff; border-color: var(--ink); }
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
.empty { color: var(--muted); font-style: italic; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .3px; vertical-align: 1px; }
.badge.vlm-on { background: #1a7f37; color: #fff; }
.badge.vlm-off { background: #3a3f44; color: #ffd33d; border: 1px solid #ffd33d; }
.badge-note { color: #8b949e; font-size: 11px; margin-left: 8px; }
footer { color: var(--muted); font-size: 12px; text-align: center; padding: 20px; }

/* value hero */
.hero { position: relative; background: linear-gradient(135deg, #0d1117, #1b2535); color: #e6edf3; border-radius: 14px; padding: 30px 30px 26px; margin: 4px 0 26px; }
.hero-toggle { position: absolute; top: 12px; right: 14px; background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.22); color: #c9d4e0; border-radius: 999px; padding: 4px 12px; font-size: 12px; cursor: pointer; }
.hero-toggle:hover { background: rgba(255,255,255,.18); }
.hero.collapsed { padding: 10px 30px; margin-bottom: 16px; }
.hero.collapsed > *:not(.hero-toggle) { display: none; }
.hero.collapsed::before { content: "procap — screen recording → written procedure"; color: #8b949e; font-size: 13px; }
.hero h2 { margin: 0 0 9px; font-size: 26px; color: #fff; letter-spacing: .2px; }
.hero .pitch { font-size: 15.5px; line-height: 1.55; color: #c9d4e0; max-width: 64ch; margin: 0 0 22px; }
.hero .pitch b { color: #fff; }
.trim { background: rgba(212,160,23,.14); border: 1px solid rgba(212,160,23,.45); color: #ffe6a3; border-radius: 10px; padding: 12px 16px; margin: 0 0 18px; font-size: 15px; }
.trim b { color: #fff; }
.transform { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.tcard { background: #fff; color: var(--ink); border-radius: 10px; padding: 12px; width: 286px; box-shadow: 0 2px 10px rgba(0,0,0,.25); }
.tcard .k { font-size: 11px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin-bottom: 8px; }
.tcard img { width: 100%; height: 156px; object-fit: cover; border-radius: 6px; background: #000; display: block; }
.tcard.out h3 { margin: 2px 0 5px; font-size: 17px; }
.tcard .meta { color: var(--muted); font-size: 12.5px; margin-top: 6px; }
.tarrow { font-size: 30px; color: #8b949e; font-weight: 700; }
.flow { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 24px; }
.flow span { background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.16); color: #c9d4e0; border-radius: 999px; padding: 5px 13px; font-size: 12.5px; }
.flow b { color: #fff; margin-right: 3px; }
.flow code, .hero code { background: rgba(255,255,255,.13); color: #fff; padding: 0 5px; border-radius: 4px; font-size: 12px; }
.runbar-label { align-self: center; color: var(--muted); font-size: 12px; margin-right: 2px; }
.layer-h { margin: 8px 0 2px; font-size: 19px; }
.layer-intro { color: var(--muted); font-size: 13.5px; max-width: 70ch; margin: 0 0 16px; }
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
dl.faq { margin: 4px 0 16px; }
dl.faq dt { font-weight: 650; margin-top: 14px; font-size: 14.5px; }
dl.faq dd { margin: 4px 0 0; color: #2c333a; font-size: 13.8px; }
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


def _render_stats(rv: RunView) -> str:
    n_gold = n_dross = 0
    if rv.segments:
        n_gold = sum(1 for s in rv.segments if s.kind == SegmentKind.GOLDEN)
        n_dross = sum(1 for s in rv.segments if s.kind == SegmentKind.DROSS)
    cells = [
        ("Duration", _fmt_t(rv.duration)),
        ("Keyframes", str(len(rv.keyframes))),
        ("Golden", str(n_gold)),
        ("Dross", str(n_dross)),
    ]
    if rv.procedure:
        cells.append(("Procedure steps", str(len(rv.procedure.steps))))
        cells.append(("Est. time", _fmt_t(rv.procedure.total_est_seconds)))
    if rv.audit:
        # Honest headline: a matched-step COUNT, not a green "%coverage" — the % read as a
        # content audit even when it was a positional count. The §4 section carries the method.
        if rv.procedure and rv.procedure.steps:
            n = len(rv.procedure.steps)
            covered = round(rv.audit.coverage * n)
            cells.append(("Doc steps matched", f"{covered}/{n}"))
        else:
            cells.append(("Doc steps matched", f"{rv.audit.coverage*100:.0f}%"))
    inner = "".join(
        f'<div class="stat"><div class="v">{_esc(v)}</div><div class="l">{_esc(l)}</div></div>'
        for l, v in cells
    )
    return f'<div class="statgrid">{inner}</div>'


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
    seg_of = _kf_segment_map(rv)
    step_of = _kf_step_map(rv)
    frames = []
    boxes = []
    for kf in rv.keyframes:
        cls = ""
        if classified:
            cls = "golden" if kf.index in golden else "dross"
        cap = (
            f'<div class="cap"><b>#{kf.index}</b> · {_fmt_t(kf.t)}'
            f'<br>Δ {kf.change_score:.3f}'
            f'{" · click" if kf.click_detected else ""}</div>'
        )
        # clickable: anchor to a :target lightbox so a skeptic can audit this exact call (no JS)
        frames.append(
            f'<div class="frame {cls}"><a href="#kf-{rv.name}-{kf.index}" data-lb="kf-{rv.name}-{kf.index}">'
            f'<img loading="lazy" src="{_img_src(rv.name, kf)}" alt="keyframe {kf.index}"></a>'
            f"{cap}</div>"
        )
        seg = seg_of.get(kf.index)
        if seg is not None:
            kind = "golden" if seg.kind == SegmentKind.GOLDEN else "dross"
            if kf.index in step_of:
                verdict = f'<span class="pill golden">KEPT</span> → became <b>step {step_of[kf.index]}</b>'
            else:
                verdict = '<span class="pill dross">DROPPED</span> as dross'
            why = f"<p><b>Why:</b> {_esc(seg.reason)} <span class='muted'>(judged by {_esc(seg.judged_by)}, conf {seg.confidence:.2f})</span></p>"
        else:
            verdict, why = "<span class='muted'>not yet classified</span>", ""
        boxes.append(
            f'<div class="lightbox" id="kf-{rv.name}-{kf.index}"><div class="box">'
            f'<a class="close" href="#" data-lbclose>✕</a>'
            f'<img src="{_img_src(rv.name, kf)}" alt="keyframe {kf.index} enlarged">'
            f'<div class="lbmeta"><h3>Keyframe #{kf.index} · {_fmt_t(kf.t)}–{_fmt_t(kf.t_end)}</h3>'
            f"<p>{verdict}</p>{why}"
            f'<p class="muted">change Δ {kf.change_score:.3f} vs previous kept frame'
            f'{" · click detected" if kf.click_detected else ""}</p></div></div></div>'
        )
    lede = (
        "Every frame the screen durably changed <em>to</em>, sampled and de-duplicated "
        "by perceptual-hash diff. Border colour shows the stage-2 verdict (gold = kept, "
        "grey = dropped). <b>Click any frame</b> to see why it was kept or dropped and which "
        "step it became — audit the call yourself."
    )
    return (
        '<section class="stage"><h2><span class="n">1</span> Keyframes</h2>'
        f'<p class="lede">{lede}</p>'
        f'<div class="film">{"".join(frames)}</div></section>'
        + "".join(boxes)
    )


def _render_segments(rv: RunView) -> str:
    if rv.segments is None:
        return (
            '<section class="stage"><h2><span class="n">2</span> Golden / dross</h2>'
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
    rows = []
    for s in rv.segments:
        kind = "golden" if s.kind == SegmentKind.GOLDEN else "dross"
        rows.append(
            "<tr>"
            f'<td>{_fmt_t(s.start_t)}–{_fmt_t(s.end_t)}</td>'
            f'<td><span class="pill {kind}">{_esc(s.kind)}</span></td>'
            f"<td>{_esc(s.reason)}</td>"
            f"<td>{s.confidence:.2f}</td>"
            f"<td>{_esc(s.judged_by)}</td>"
            "</tr>"
        )
    lede = (
        "Contiguous stretches classified <b>golden</b> (a consequential action worth "
        "keeping) or <b>dross</b> (a mis-click reverted, mouse wander, dead time). The "
        "revert-detection heuristic is always on; a VLM refines it when a key is present."
    )
    legend = (
        '<div class="legend">'
        '<span><i style="background:var(--golden)"></i>golden — kept</span>'
        '<span><i style="background:var(--dross)"></i>dross — dropped</span></div>'
    )
    table = (
        '<table class="segtable"><thead><tr>'
        "<th>Span</th><th>Kind</th><th>Reason</th><th>Conf.</th><th>By</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return (
        '<section class="stage"><h2><span class="n">2</span> Golden / dross</h2>'
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
    metrics = "".join(
        f'<div class="stat"><div class="v">{sc.get(k, 0):.2f}</div><div class="l">{lbl}</div></div>'
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
        '<section class="stage"><h2><span class="n">2·eval</span> Accuracy vs ground truth</h2>'
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


def _render_procedure(rv: RunView) -> str:
    if rv.procedure is None:
        return (
            '<section class="stage"><h2><span class="n">3</span> Procedure</h2>'
            '<p class="empty">Not run yet — run the <code>procedure</code> stage.</p></section>'
        )
    kbi = rv.keyframe_by_index
    steps = []
    for st in rv.procedure.steps:
        # thumbnails link to the same :target lightboxes the keyframe stage emits, so a user
        # can audit "is this really one action?" right where they evaluate the output.
        thumbs = "".join(
            f'<a href="#kf-{rv.name}-{i}" data-lb="kf-{rv.name}-{i}"><img loading="lazy" src="{_img_src(rv.name, kbi[i])}" alt="kf {i}"></a>'
            for i in st.keyframe_indexes
            if i in kbi
        )
        title = _esc(st.title)
        if "[fill in" in st.title:
            title = f'<span class="todo">{title}</span>'
        intent = (
            f'<div class="intent">{_esc(st.intent)}</div>' if st.intent else ""
        )
        desc = f'<div class="intent">{_esc(st.description)}</div>' if st.description else ""
        if st.held_seconds > 0:
            active = max(0.0, st.est_seconds - st.held_seconds)
            est_html = (
                f"est {_fmt_t(st.est_seconds)} "
                f'<span class="held">(≤{_fmt_t(active)} active + {_fmt_t(st.held_seconds)} '
                "held — attribution unknown)</span>"
            )
        else:
            est_html = f"est {_fmt_t(st.est_seconds)}"
        steps.append(
            '<div class="step">'
            f'<div class="num">{st.index + 1}</div>'
            '<div class="body">'
            f"<h3>{title}</h3>"
            f'<div class="meta">{_fmt_t(st.start_t)}–{_fmt_t(st.end_t)} · '
            f"{est_html} · keyframes {', '.join('#'+str(i) for i in st.keyframe_indexes)}</div>"
            f"{desc}{intent}</div>"
            f'<div class="thumbs">{thumbs}</div>'
            "</div>"
        )
    lede = (
        "One ordered step per kept keyframe, with a time estimate carried from the "
        "segment span. A stretch held longer than the active-action threshold is reported "
        "honestly as <span class='held'>≤X active + Y held (attribution unknown)</span> — "
        "pixels alone can't tell active work from waiting. Intent / titles are the manual "
        "fill-in the operator supplies, or the VLM proposes when enabled (grouping "
        "consecutive keyframes into one step is later-phase work, not yet built)."
    )
    return (
        '<section class="stage"><h2><span class="n">3</span> Procedure '
        f'— {_esc(rv.procedure.title)}</h2>'
        f'<p class="lede">{lede}</p>{"".join(steps)}</section>'
    )


def _render_audit(rv: RunView) -> str:
    if rv.audit is None:
        return ""  # audit is optional; omit the section entirely when absent
    cov = max(0.0, min(1.0, rv.audit.coverage))
    if rv.audit.findings:
        fin = "".join(
            '<div class="finding">'
            f'<div class="k">{_esc(f.kind)}'
            f'{" · step " + str(f.procedure_step_index + 1) if f.procedure_step_index is not None else ""}'
            f'{" · " + _esc(f.doc_ref) if f.doc_ref else ""}</div>'
            f"{_esc(f.detail)}</div>"
            for f in rv.audit.findings
        )
    else:
        fin = '<p class="empty">No gaps found — the written doc covers every generated step.</p>'
    # Honesty: how the audit aligned steps bounds what it can find. Label it by method
    # (audit.py records AuditReport.method) — never render a count ratio as a content audit.
    doc = _esc(rv.audit.written_doc)
    method = rv.audit.method
    if method == "vlm":
        lede = (
            f"<b>VLM content match</b> against <code>{doc}</code>: each generated step matched "
            "to the doc step describing the same action by meaning — missing, mis-ordered, or "
            "under-documented (thinly covered)."
        )
        cov_label = "of generated steps semantically matched to a doc step."
    elif method == "lexical":
        floor = rv.meta.get("match_floor")
        floor_txt = f" (match floor {floor})" if floor is not None else ""
        lede = (
            f"<b>Offline lexical content match</b> against <code>{doc}</code>{floor_txt} — "
            "<b>no model</b>: each step matched to the doc step with the highest word overlap, "
            "then order checked. This proves content audit is <em>content-gated, not "
            "VLM-gated</em> (here the content is manually-filled intents). It is <b>lexical, "
            "not semantic</b> — it can mis-pair on shared vocabulary; <em>under-documented</em> "
            "(a thinness judgement) is deliberately left to the VLM."
        )
        cov_label = "of generated steps lexically matched to a doc step."
    else:  # count
        placeholder_titles = bool(
            rv.procedure and any("[fill in" in s.title for s in rv.procedure.steps)
        )
        lede = (
            f"<b>Structural check only</b> (no content): generated steps aligned to "
            f"<code>{doc}</code> <b>by position and count</b>. Mis-order / under-documented "
            "detection is <b>content-dependent</b> — it needs step titles/intents (from the "
            "VLM <em>or</em> a manual fill-in, not VLM-only); with placeholder titles there is "
            "nothing to match here yet."
        )
        cov_label = (
            "structural (count) alignment — <b>not</b> a content match."
            + (
                " Generated titles are still <span class='todo'>[fill in]</span> placeholders, "
                "so there is no content to match by construction."
                if placeholder_titles
                else ""
            )
        )
    return (
        '<section class="stage"><h2><span class="n">4</span> Audit vs written doc</h2>'
        f'<p class="lede">{lede}</p>'
        f'<div class="cov"><span style="width:{cov*100:.1f}%"></span></div>'
        f'<p style="margin:6px 0 0;font-size:13px;color:var(--muted)">'
        f"{cov*100:.0f}% {cov_label}</p>"
        f"{fin}</section>"
    )


def _render_how() -> str:
    """Plain-language 'how it works' — the pipeline as a user reads it, collapsed by default."""
    steps = [
        ("Decompose", "splits your recording into the handful of frames where the screen "
         "actually changed (ignoring mouse jiggle and re-renders)."),
        ("Keep or drop", "judges each stretch golden (a real action) or dross (a mis-click you "
         "undid, idle time, dead air) — so fumbles don't end up in your SOP."),
        ("Write timed steps", "turns each kept stretch into one ordered, time-estimated step. "
         "Wording is yours to fill in, or a vision model drafts it when you add a key."),
        ("Audit (optional)", "compares the result against an SOP you already have, flagging "
         "steps it's missing, out of order, or covers only thinly."),
    ]
    lis = "".join(f"<li><b>{t}</b> — {d}</li>" for t, d in steps)
    return (
        '<details class="disc"><summary>How it works</summary>'
        '<p class="lede">Four stages, all running offline by default; a vision-model key only '
        "enriches wording and the audit.</p>"
        f"<ol class='how'>{lis}</ol></details>"
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
         "It's measured against ground truth, shown in the “Accuracy vs ground truth” panel "
         "above, not asserted." + acc),
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
    """Land on a run that has ground-truth labels, so the accuracy overlay (the proof the
    keep/drop call works) leads — not the alphabetically-first run, which buries it."""
    for p in runs:
        if RunView(p).labels:
            return p
    return runs[0] if runs else None


def _pick_showcase(runs: list[Path]):
    """(RunView, ProcedureStep, Keyframe) for an input→output example with REAL step text
    (filled intent or a VLM/non-placeholder title), or None. Never returns a placeholder step:
    the hero must not present a `[fill in]` slot as if it were a generated result."""
    for p in runs:
        rv = RunView(p)
        if not rv.procedure or not rv.procedure.steps:
            continue
        kbi = rv.keyframe_by_index
        for st in rv.procedure.steps:
            kf = next((kbi[i] for i in st.keyframe_indexes if i in kbi), None)
            if kf is None:
                continue
            if "[fill in" not in st.title or st.intent.strip():
                return rv, st, kf
    return None


def _first_renderable_step(rv: RunView):
    """(step, keyframe) for the first step with an existing keyframe image, or (None, None)."""
    if not rv.procedure:
        return None, None
    kbi = rv.keyframe_by_index
    for st in rv.procedure.steps:
        kf = next((kbi[i] for i in st.keyframe_indexes if i in kbi), None)
        if kf is not None:
            return st, kf
    return None, None


def _render_hero(runs: list[Path]) -> str:
    """The value pitch, the transform at a glance, and the usage flow — outside-in framing for a
    newcomer. Everything shown is something the pipeline genuinely did on a real run: the trim
    story is deterministic offline, and the step transform appears only with real step text (no
    fabricated title — the prior failure mode)."""
    pitch = (
        "procap watches a screen recording of you doing a task and writes the step-by-step "
        "procedure for you — <b>timed, ordered, with the mis-clicks and dead time already "
        "trimmed</b>. Record the task once instead of hand-writing the SOP."
    )
    show = _pick_showcase(runs)
    hero_rv = show[0] if show else (RunView(_default_active(runs)) if runs else None)

    # Trim story — TRUE offline for any video (dross removal is deterministic, no key needed).
    trim = ""
    if hero_rv and hero_rv.segments is not None and hero_rv.procedure is not None:
        n_dross = sum(1 for s in hero_rv.segments if s.kind == SegmentKind.DROSS)
        n_steps = len(hero_rv.procedure.steps)
        est = hero_rv.procedure.total_est_seconds
        dropped = (
            f"dropped <b>{n_dross}</b> dead-end / idle stretch{'es' if n_dross != 1 else ''}"
            if n_dross else "found no fumbles to drop"
        )
        trim = (
            '<div class="trim">From <b>'
            f"{_fmt_t(hero_rv.duration)}</b> of recording, procap {dropped} and wrote "
            f"<b>{n_steps}</b> timed steps ({_fmt_t(est)} total) — a draft procedure, no "
            "hand-writing.</div>"
        )

    # Step transform — only with REAL step text; otherwise an honest draft slot (no fabrication).
    transform = ""
    if show:
        rv, st, kf = show
        held = ""
        if st.held_seconds > 0:
            active = max(0.0, st.est_seconds - st.held_seconds)
            held = f" · ≤{_fmt_t(active)} active + {_fmt_t(st.held_seconds)} held"
        out = (
            '<div class="tcard out"><div class="k">Output — a procedure step</div>'
            f"<h3>{_esc(st.intent.strip() or st.title)}</h3>"
            f'<div class="meta">est {_fmt_t(st.est_seconds)}{held} · auto-timed from the recording</div></div>'
        )
    elif hero_rv:
        st, kf = _first_renderable_step(hero_rv)
        if kf is not None:
            out = (
                '<div class="tcard out"><div class="k">Output — a procedure step</div>'
                '<h3 class="todo">[ you write the action, or a vision model fills it ]</h3>'
                f'<div class="meta">timed slot {_fmt_t(st.start_t)}–{_fmt_t(st.end_t)} · est '
                f"{_fmt_t(st.est_seconds)} — the timing & ordering are automatic; the wording is the "
                "one thing left to you</div></div>"
            )
        else:
            kf = None
    if show or (hero_rv and kf is not None):
        transform = (
            '<div class="transform">'
            '<div class="tcard in"><div class="k">Input — a moment from your recording</div>'
            f'<img src="{_img_src((show[0] if show else hero_rv).name, kf)}" alt="recording frame">'
            f'<div class="meta">screen at {_fmt_t(kf.t)}</div></div>'
            f'<div class="tarrow">→</div>{out}</div>'
        )

    flow = (
        '<div class="flow">'
        "<span><b>1</b> Record your screen</span>"
        "<span><b>2</b> <code>procap run</code></span>"
        "<span><b>3</b> Review the draft (this page)</span>"
        "<span><b>4</b> Export the <code>.md</code> procedure</span>"
        "</div>"
    )
    return (
        '<section class="hero">'
        '<button id="hero-toggle" class="hero-toggle" type="button">Hide intro ▴</button>'
        '<h2>Turn a screen recording into a written procedure</h2>'
        f'<p class="pitch">{pitch}</p>{trim}{transform}{flow}</section>'
    )


def render_page(runs: list[Path], active: Path | None) -> str:
    runbar = "".join(
        f'<a class="{"active" if active and p == active else ""}" '
        f'href="/?run={quote(p.name)}">{_esc(p.name)}</a>'
        for p in runs
    )
    if active is None:
        body = '<p class="empty">No runs found under the runs/ directory. Generate one with <code>procap run &lt;video&gt;</code>.</p>'
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
        body = (
            '<h2 class="layer-h">Inspect the example, stage by stage</h2>'
            '<p class="layer-intro">Don\'t take the pitch on faith — here is a real run end '
            "to end, so you can judge the quality yourself: which moments it kept vs. dropped, "
            "how accurate that call is against ground truth, how it timed each step, and how it "
            "audits against an existing written doc.</p>"
            '<section class="stage"><h2>Overview</h2>'
            '<p class="lede">A screen recording run through procap: video decomposed into '
            "keyframes, stretches judged golden or dross, a time-estimated procedure "
            "synthesized from the golden ones, then audited against a written doc.</p>"
            f"{_render_stats(rv)}</section>"
            + _render_keyframes(rv)
            + _render_segments(rv)
            + _render_eval(rv)
            + _render_procedure(rv)
            + _render_audit(rv)
            + _render_how()
            + _render_faq(rv)
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>procap demo{(' · ' + _esc(active.name)) if active else ''}</title>
<style>{CSS}</style></head>
<body>
<header class="top"><h1>procap</h1>
<div class="tag">screen recording → written, time-estimated procedure</div></header>
<div class="wrap">
{_render_hero(runs) if runs else ''}
{('<div class="runbar"><span class="runbar-label">Example run:</span>' + runbar + '</div>') if runs else ''}
{('<header style="margin-bottom:16px">' + sub + '</header>') if sub else ''}
{body}
<footer>procap demo · stdlib http.server · artifacts under runs/</footer>
</div>
<script>
/* Progressive enhancement only — the page is fully usable with JS off (lightboxes fall back
   to :target). Adds: scroll preservation across run-switches, in-place lightbox open/close
   (no jump to top), and a persistent "hide intro" toggle. No dependencies. */
(function () {{
  // 1) keep scroll position across the full-page reload a run-switch triggers
  try {{
    var SK = "procap-scroll";
    var y = sessionStorage.getItem(SK);
    if (y !== null) window.scrollTo(0, parseInt(y, 10));
    window.addEventListener("beforeunload", function () {{
      sessionStorage.setItem(SK, String(window.scrollY));
    }});
  }} catch (e) {{}}

  // 2) lightbox open/close without changing the hash (so the page never scrolls to top)
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
    }}
  }});
  document.addEventListener("keydown", function (ev) {{
    if (ev.key === "Escape") {{
      var open = document.querySelector(".lightbox.show");
      if (open) open.classList.remove("show");
    }}
  }});

  // 3) persistent "hide intro" toggle
  try {{
    var HK = "procap-hero-hidden";
    var hero = document.querySelector(".hero");
    var btn = document.getElementById("hero-toggle");
    function apply(h) {{
      if (hero) hero.classList.toggle("collapsed", h);
      if (btn) btn.textContent = h ? "Show intro ▾" : "Hide intro ▴";
    }}
    if (btn) {{
      apply(localStorage.getItem(HK) === "1");
      btn.addEventListener("click", function () {{
        var h = localStorage.getItem(HK) !== "1";
        localStorage.setItem(HK, h ? "1" : "0");
        apply(h);
      }});
    }}
  }} catch (e) {{}}
}})();
</script>
</body></html>"""


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

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path == "/img":
                return self._serve_img(qs)
            if parsed.path in ("/", "/index.html"):
                return self._serve_index(qs)
            self._send(404, b"not found", "text/plain")

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
