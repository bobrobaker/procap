"""Stage 1 — decompose a screenshare into keyframes at moments of durable change.

A screen recording does not encode clicks in its pixels, so "extract a frame when
something was clicked" becomes "extract a frame when the UI durably changed *to* a new
state." We sample at a fixed fps, partition the sampled frames into *stable runs*
(consecutive frames with a near-zero changed-pixel fraction), drop runs too short to be a real
held state (transient animation/scroll frames), and emit one Keyframe per surviving run.

This naturally filters mid-transition blur and gives each keyframe a [t, t_end) dwell
window — which stage 2 (golden) uses for revert-detection and dwell heuristics.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from .imageutil import load_gray, changed_fraction, phash as phash_of
from .model import Keyframe
from .run import Run


def _ffprobe_duration(video: str | Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def sample_frames(video: str | Path, frames_dir: Path, fps: float) -> list[tuple[int, float, Path]]:
    """ffmpeg-sample `video` at `fps` into frames_dir; return (index, t_seconds, path)."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("frame_*.png"):
        old.unlink()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video),
         "-vf", f"fps={fps}", "-q:v", "2", str(frames_dir / "frame_%06d.png")],
        check=True,
    )
    out = []
    for p in sorted(frames_dir.glob("frame_*.png")):
        n = int(p.stem.split("_")[1])           # 1-indexed from ffmpeg
        t = (n - 1) / fps
        out.append((n - 1, t, p))
    return out


def extract_keyframes(
    video: str | Path,
    run: Run | None = None,
    fps: float = 2.0,
    change_threshold: float = 0.0025,
    min_dwell_s: float = 0.6,
    keep_frames: bool = False,
) -> list[Keyframe]:
    """Decompose `video` into Keyframes. Writes keyframes.json + meta.json to the run.

    A new sampled frame starts a new *run* when its changed-pixel fraction vs the current
    run's anchor exceeds `change_threshold`. Runs shorter than `min_dwell_s` are transients
    (mid-transition frames, a cursor passing through) and are dropped — their state never
    settled. One Keyframe is emitted per surviving run.
    """
    video = Path(video)
    run = run or Run.for_video(video)
    run.ensure()

    sampled = sample_frames(video, run.frames_dir, fps)
    if not sampled:
        raise RuntimeError(f"no frames sampled from {video} — is it a valid video?")

    grays = {idx: load_gray(str(p)) for idx, _t, p in sampled}

    # Partition into stable runs: a boundary opens where the change vs the run anchor exceeds
    # the threshold. Comparing to the anchor (not the previous frame) keeps a slowly drifting
    # cursor from accumulating into a false boundary.
    runs: list[dict] = []
    cur = {"start_i": 0, "anchor_idx": sampled[0][0]}
    for pos in range(1, len(sampled)):
        idx = sampled[pos][0]
        if changed_fraction(grays[cur["anchor_idx"]], grays[idx]) > change_threshold:
            cur["end_pos"] = pos - 1
            runs.append(cur)
            cur = {"start_i": pos, "anchor_idx": idx}
    cur["end_pos"] = len(sampled) - 1
    runs.append(cur)

    # Materialize timestamps + dwell; drop sub-dwell transients.
    frame_period = 1.0 / fps
    kept: list[dict] = []
    for r in runs:
        t_start = sampled[r["start_i"]][1]
        t_end = sampled[r["end_pos"]][1] + frame_period
        if (t_end - t_start) < min_dwell_s and len(runs) > 1:
            continue
        r["t_start"], r["t_end"] = t_start, t_end
        kept.append(r)

    keyframes: list[Keyframe] = []
    prev_anchor = None
    for ki, r in enumerate(kept):
        idx, _t, src_path = sampled[r["start_i"]]
        change = 1.0 if prev_anchor is None else changed_fraction(grays[prev_anchor], grays[idx])
        dst = run.keyframes_dir / f"kf_{ki:04d}.png"
        Image.open(src_path).save(dst)
        keyframes.append(Keyframe(
            index=ki, t=round(r["t_start"], 3), t_end=round(r["t_end"], 3),
            path=str(dst), change_score=round(min(1.0, change), 4), phash=phash_of(src_path),
        ))
        prev_anchor = idx

    if not keep_frames:
        for p in run.frames_dir.glob("frame_*.png"):
            p.unlink()

    run.write_meta(
        source_video=str(video), fps_sampled=fps,
        duration=_ffprobe_duration(video),
        n_sampled=len(sampled), n_keyframes=len(keyframes),
        change_threshold=change_threshold, min_dwell_s=min_dwell_s,
    )
    run.write_keyframes(keyframes)
    return keyframes
