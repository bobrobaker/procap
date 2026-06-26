"""End-to-end pipeline smoke test: a real clip through extract -> golden -> procedure.

Unlike the per-stage tests (which assert known labels on the synthetic clip), this asserts only
the *artifact contract* holds on whatever clip is available: nothing crashes, and each stage
writes a non-empty, well-formed artifact the next stage can consume. It is the "does the whole
thing run on real-ish video" check the synthetic unit tests don't give.

Clip selection, in order of preference:
  1. a real downloaded clip under corpus/real/ (the robustness target; gitignored, present only
     after `corpus/fetch_real.py`),
  2. else the synthetic clip (regenerated on demand, exactly like the conftest fixture).
The clip is trimmed to a few seconds with `ffmpeg -t` so CI stays fast. Skips (does not fail) if
no clip can be obtained or ffmpeg is unavailable — a missing optional download must not redden CI.
"""
from __future__ import annotations

import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REAL_DIR = ROOT / "corpus" / "real"
SYNTH = ROOT / "corpus" / "synthetic" / "labeled_demo.mp4"
TRIM_SECONDS = 7.0


def _obtain_source_clip() -> Path | None:
    """A real clip if one was downloaded, else the (regenerated) synthetic clip, else None."""
    real = sorted(REAL_DIR.glob("*.mp4")) if REAL_DIR.exists() else []
    if real:
        return real[0]
    if not SYNTH.exists():
        try:
            runpy.run_path(str(ROOT / "corpus" / "make_synthetic.py"), run_name="__main__")
        except Exception:
            return None
    return SYNTH if SYNTH.exists() else None


@pytest.fixture(scope="module")
def trimmed_clip(tmp_path_factory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    src = _obtain_source_clip()
    if src is None:
        pytest.skip("no clip available (download corpus/real or generate synthetic)")
    out = tmp_path_factory.mktemp("clip") / "slice.mp4"
    # Re-encode (not stream-copy) so the trim is frame-accurate and decodes cleanly downstream.
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", str(TRIM_SECONDS), "-an",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out.exists():
        pytest.skip(f"ffmpeg trim failed: {proc.stderr[-300:]}")
    return out


def test_full_pipeline_artifact_contract(trimmed_clip, tmp_path):
    """extract -> golden -> procedure produces non-empty, well-formed artifacts and ≥1 step."""
    from procap import extract as extract_stage
    from procap import golden as golden_stage
    from procap import procedure as procedure_stage
    from procap.run import Run

    run = Run(tmp_path / "run")

    # Stage 1: extract — non-empty keyframes.json with the fields downstream stages read.
    kfs = extract_stage.extract_keyframes(trimmed_clip, run=run)
    assert kfs, "extract produced no keyframes"
    assert (run.dir / "keyframes.json").stat().st_size > 0
    reread = run.read_keyframes()
    assert len(reread) == len(kfs)
    for k in reread:
        assert k.t <= k.t_end and k.phash and Path(k.path).exists()

    # Stage 2: golden — non-empty segments.json; every keyframe lands in exactly one segment.
    segs = golden_stage.refine_with_vlm(golden_stage.classify(kfs), kfs)
    run.write_segments(segs)
    assert segs, "golden produced no segments"
    assert (run.dir / "segments.json").stat().st_size > 0
    covered = [i for s in run.read_segments() for i in s.keyframe_indexes]
    assert sorted(covered) == [k.index for k in kfs], "segments must partition the keyframes"
    assert any(s.kind == "golden" for s in segs), "expected at least one golden segment"

    # Stage 3: procedure — Procedure with ≥1 step, and a procedure.md that renders those steps.
    meta = run.read_meta()
    proc = procedure_stage.synthesize(segs, source_video=meta["source_video"])
    run.write_procedure(proc)
    md = procedure_stage.render_markdown(proc)
    (run.dir / "procedure.md").write_text(md)

    assert proc.steps, "procedure has no steps"
    assert run.read_procedure().steps, "procedure.json round-trip lost its steps"
    md_text = (run.dir / "procedure.md").read_text()
    assert md_text.strip(), "procedure.md is empty"
    # Each step renders as a numbered "## N. ..." heading; there must be at least as many as steps.
    headings = [ln for ln in md_text.splitlines() if ln.startswith("## ")]
    assert len(headings) >= len(proc.steps) >= 1, headings
