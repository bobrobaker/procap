"""Frozen data contracts shared by every pipeline stage.

These dataclasses are the interface between stages: each stage reads the previous
stage's list[...] from a JSON file and writes its own (see procap.run). Treat changes
here as interface changes — they ripple through extract/golden/procedure/audit.

Every type is JSON round-trippable via to_dict()/from_dict(), so artifacts on disk are
the single source of truth and stages can be developed and re-run independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class SegmentKind(str, Enum):
    """Whether a stretch of the recording is worth keeping."""
    GOLDEN = "golden"   # a consequential action: keep it, it becomes a procedure step
    DROSS = "dross"     # mis-click reverted, mouse wander, dead time: drop it


@dataclass
class Keyframe:
    """A frame the recording durably changed *to*, held until the next keyframe.

    `t` is the timestamp (seconds) the new state appeared; the state is considered
    current over [t, t_end). `change_score` is the diff magnitude vs the previous
    keyframe (0..1). `phash` is the perceptual hash (hex string) used downstream for
    revert-detection. `path` points at the saved PNG.
    """
    index: int
    t: float
    t_end: float
    path: str
    change_score: float
    phash: str
    click_detected: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Keyframe":
        return cls(**d)


@dataclass
class Segment:
    """A contiguous run of keyframes judged golden or dross, with the reason why."""
    start_t: float
    end_t: float
    keyframe_indexes: list[int]
    kind: str                 # a SegmentKind value
    reason: str               # why this kind (e.g. "reverted to state @12.0s", "mouse wander")
    confidence: float = 0.0   # 0..1; heuristic certainty, or VLM agreement
    judged_by: str = "heuristic"   # "heuristic" | "vlm"

    @property
    def duration(self) -> float:
        return max(0.0, self.end_t - self.start_t)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(**d)


@dataclass
class ProcedureStep:
    """One step of the synthesized procedure, sourced from one golden segment."""
    index: int
    title: str
    description: str
    keyframe_indexes: list[int]
    start_t: float
    end_t: float
    est_seconds: float
    intent: str = ""          # manual fill-in: "what you are doing here" (the spec's prompt)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProcedureStep":
        return cls(**d)


@dataclass
class Procedure:
    title: str
    source_video: str
    steps: list[ProcedureStep] = field(default_factory=list)
    total_est_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "source_video": self.source_video,
            "total_est_seconds": self.total_est_seconds,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Procedure":
        return cls(
            title=d["title"],
            source_video=d["source_video"],
            total_est_seconds=d.get("total_est_seconds", 0.0),
            steps=[ProcedureStep.from_dict(s) for s in d.get("steps", [])],
        )


class FindingKind(str, Enum):
    """How a written procedure diverges from the generated one."""
    MISSING_STEP = "missing_step"           # generated step absent from the written doc
    OUT_OF_ORDER = "out_of_order"           # written doc orders steps differently
    UNDER_DOCUMENTED = "under_documented"   # written doc mentions it but thinly
    EXTRA_IN_DOC = "extra_in_doc"           # written doc has a step the video doesn't show


@dataclass
class AuditFinding:
    kind: str                          # a FindingKind value
    detail: str
    procedure_step_index: Optional[int] = None
    doc_ref: Optional[str] = None      # heading/line in the written doc

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AuditFinding":
        return cls(**d)


@dataclass
class AuditReport:
    written_doc: str
    coverage: float                    # fraction of generated steps the doc covers (0..1)
    findings: list[AuditFinding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "written_doc": self.written_doc,
            "coverage": self.coverage,
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AuditReport":
        return cls(
            written_doc=d["written_doc"],
            coverage=d.get("coverage", 0.0),
            findings=[AuditFinding.from_dict(f) for f in d.get("findings", [])],
        )
