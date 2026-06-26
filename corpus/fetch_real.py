"""Download ONE short, screen-only recording of a technical GUI for the robustness corpus.

The synthetic clip (`make_synthetic.py`) proves the heuristics recover *known* labels. This
script fetches a real screencast — uncontrolled framerate, compression noise, real dialogs and
window chrome — so the pipeline can be checked against content nobody scripted.

What "good" looks like here: mostly screen pixels (software UI, not a webcam/talking head),
operating a technical/engineering tool (EDA, oscilloscope/analyzer software, CNC/3D-printer
control, SCADA/LabVIEW/PLC HMI, a lab-instrument console). The default is a ~3-minute KiCad
session (schematic + PCB editor + 3D viewer + several dialogs) — squarely the target domain.

Output: a single .mp4 under `corpus/real/` (gitignored — real downloads are large/copyright).

    ../.venv/bin/python fetch_real.py                 # default KiCad clip, trimmed to 180s
    ../.venv/bin/python fetch_real.py <youtube-url>   # any screen-only technical-GUI recording
    ../.venv/bin/python fetch_real.py <url> --max-seconds 60 --name myclip

Requires `yt-dlp` and `ffmpeg`/`ffprobe` on PATH. yt-dlp lives in the venv:
`../.venv/bin/pip install yt-dlp` if missing.

NOTE on trimming: we download the *full* stream and trim with a separate `ffmpeg -t` re-encode
rather than yt-dlp's `--download-sections`. In this environment the section-cut path crashes
ffmpeg with SIGSEGV (exit -11) on the keyframe-accurate seek; a plain decode-and-trim is stable.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# A genuine screen-only KiCad 6 session: schematic editor, PCB editor, 3D viewer, and several
# modal dialogs opening/closing — the kind of durable-state-change content the keyframer targets.
DEFAULT_URL = "https://www.youtube.com/watch?v=G3PZiaexLuI"
DEFAULT_NAME = "kicad_demo"
OUT_DIR = Path(__file__).parent / "real"


def _require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"error: `{tool}` not found on PATH — install ffmpeg/ffprobe.")


def _ytdlp_cmd() -> list[str]:
    """Invoke yt-dlp via the running interpreter's module, not a PATH console script.

    Running `<venv>/bin/python fetch_real.py` does NOT put the venv's bin/ on PATH, so the
    `yt-dlp` console script is invisible to shutil.which even when it is installed in the venv.
    `python -m yt_dlp` resolves it from the same interpreter every time.
    """
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        sys.exit(
            "error: yt-dlp is not installed in this interpreter.\n"
            f"  install: {sys.executable} -m pip install yt-dlp"
        )
    return [sys.executable, "-m", "yt_dlp"]


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def fetch(url: str, name: str, max_seconds: float, out_dir: Path) -> Path:
    """Download `url`, trim to `max_seconds`, strip audio; write out_dir/<name>.mp4."""
    ytdlp = _ytdlp_cmd()
    _require_ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{name}.mp4"

    tmp = Path(tempfile.mkdtemp(prefix="procap_fetch_"))
    try:
        raw = tmp / "raw.mp4"
        # Cap resolution: 720p is plenty for change-detection and keeps the download small.
        # Format fallbacks: best mp4 video-only, then best mp4 muxed, then best anything.
        proc = subprocess.run(
            ytdlp + ["--no-playlist", "--no-warnings",
                     "-f", "bv*[height<=720][ext=mp4]/b[ext=mp4]/b",
                     "-o", str(raw), url],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not raw.exists():
            sys.exit(
                "error: download failed.\n"
                f"  url: {url}\n"
                f"  yt-dlp stderr (tail):\n    "
                + "\n    ".join((proc.stderr or proc.stdout or "(no output)").splitlines()[-8:])
                + "\n\nIf downloads are blocked in this environment, fall back to a richer "
                  "synthetic clip — see corpus/make_synthetic.py and the findings decision doc."
            )

        full = _duration(raw)
        # Trim with a separate re-encode (yt-dlp --download-sections SIGSEGVs here; see module docstring).
        # Drop audio (-an): a procedure is built from pixels, audio is dead weight.
        if full > max_seconds:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(raw),
                 "-t", str(max_seconds), "-an",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(final)],
                check=True,
            )
        else:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-an",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(final)],
                check=True,
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    dur = _duration(final)
    print(f"wrote {final} ({dur:.1f}s, {final.stat().st_size / 1e6:.1f} MB) from {url}")
    print(f"next: ../.venv/bin/python -m procap.cli run {final}")
    return final


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", nargs="?", default=DEFAULT_URL,
                   help=f"video URL (default: a KiCad screencast, {DEFAULT_URL})")
    p.add_argument("--name", default=DEFAULT_NAME, help="output stem under the out dir")
    p.add_argument("--max-seconds", type=float, default=180.0,
                   help="trim the clip to at most this many seconds (default 180)")
    p.add_argument("--out-dir", default=str(OUT_DIR), help="output dir (default corpus/real)")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])
    fetch(args.url, args.name, args.max_seconds, Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
