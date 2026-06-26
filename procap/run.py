"""The on-disk run dir: where stages persist and exchange artifacts.

Layout of a run dir (default: runs/<video-stem>/):
    meta.json         # source video path, fps sampled, duration
    frames/           # every sampled PNG (extract may keep or prune these)
    keyframes/        # the kept keyframe PNGs
    keyframes.json    # list[Keyframe]   (extract -> golden)
    segments.json     # list[Segment]    (golden -> procedure)
    procedure.json    # Procedure        (procedure stage)
    procedure.md      # human-readable render
    audit.json        # AuditReport      (audit stage)
    audit.md          # human-readable render

Each stage reads what it needs and writes its own file; nothing is held in memory
across stages. This is the contract that lets stages be built and re-run independently.
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import Keyframe, Segment, Procedure, AuditReport


class Run:
    def __init__(self, run_dir: str | Path):
        self.dir = Path(run_dir)

    # --- dir management ---------------------------------------------------
    def ensure(self) -> "Run":
        self.dir.mkdir(parents=True, exist_ok=True)
        self.frames_dir.mkdir(exist_ok=True)
        self.keyframes_dir.mkdir(exist_ok=True)
        return self

    @property
    def frames_dir(self) -> Path:
        return self.dir / "frames"

    @property
    def keyframes_dir(self) -> Path:
        return self.dir / "keyframes"

    @classmethod
    def for_video(cls, video: str | Path, base: str | Path = "runs") -> "Run":
        stem = Path(video).stem
        return cls(Path(base) / stem).ensure()

    # --- low-level json ---------------------------------------------------
    def _read(self, name: str):
        p = self.dir / name
        if not p.exists():
            raise FileNotFoundError(
                f"{p} missing — run the prior stage first (run dir: {self.dir})"
            )
        return json.loads(p.read_text())

    def _write(self, name: str, obj) -> Path:
        p = self.dir / name
        p.write_text(json.dumps(obj, indent=2))
        return p

    # --- meta -------------------------------------------------------------
    def write_meta(self, **kw) -> Path:
        return self._write("meta.json", kw)

    def read_meta(self) -> dict:
        return self._read("meta.json")

    # --- keyframes (stage 1) ---------------------------------------------
    def write_keyframes(self, keyframes: list[Keyframe]) -> Path:
        return self._write("keyframes.json", [k.to_dict() for k in keyframes])

    def read_keyframes(self) -> list[Keyframe]:
        return [Keyframe.from_dict(d) for d in self._read("keyframes.json")]

    # --- segments (stage 2) ----------------------------------------------
    def write_segments(self, segments: list[Segment]) -> Path:
        return self._write("segments.json", [s.to_dict() for s in segments])

    def read_segments(self) -> list[Segment]:
        return [Segment.from_dict(d) for d in self._read("segments.json")]

    # --- procedure (stage 3) ---------------------------------------------
    def write_procedure(self, procedure: Procedure) -> Path:
        return self._write("procedure.json", procedure.to_dict())

    def read_procedure(self) -> Procedure:
        return Procedure.from_dict(self._read("procedure.json"))

    # --- audit (stage 3) -------------------------------------------------
    def write_audit(self, report: AuditReport) -> Path:
        return self._write("audit.json", report.to_dict())

    def read_audit(self) -> AuditReport:
        return AuditReport.from_dict(self._read("audit.json"))
