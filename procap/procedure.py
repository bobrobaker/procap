"""Stage 3a — synthesize a time-estimated procedure from the golden segments.

One procedure step per golden segment. Durations are real (segment wall-time from the
keyframe timestamps). Titles/descriptions come from the VLM when available; offline they
are `[fill in]` placeholders — which doubles as the spec's invitation to manually annotate
"what you are doing here" (ProcedureStep.intent).

Baseline spine; the stage-3 workstream adds the VLM titling/description path and the
optional per-step complexity adjustment to the time estimate.
"""
from __future__ import annotations

from pathlib import Path

from .model import Procedure, ProcedureStep, Segment, SegmentKind
from .vlm import VLM

_FILL = "[fill in: what are you doing in this step?]"


def synthesize(
    segments: list[Segment],
    source_video: str,
    title: str | None = None,
    vlm: VLM | None = None,
) -> Procedure:
    """Golden segments -> Procedure. Dross segments are skipped."""
    vlm = vlm or VLM()
    golden = [s for s in segments if s.kind == SegmentKind.GOLDEN.value]

    steps: list[ProcedureStep] = []
    for i, seg in enumerate(golden):
        step_title, desc = _describe(seg, i, vlm)
        steps.append(ProcedureStep(
            index=i,
            title=step_title,
            description=desc,
            keyframe_indexes=list(seg.keyframe_indexes),
            start_t=seg.start_t,
            end_t=seg.end_t,
            est_seconds=round(seg.duration, 1),
            intent="",
        ))

    total = round(sum(s.est_seconds for s in steps), 1)
    return Procedure(
        title=title or f"Procedure from {Path(source_video).name}",
        source_video=source_video,
        steps=steps,
        total_est_seconds=total,
    )


def _describe(seg: Segment, i: int, vlm: VLM) -> tuple[str, str]:
    """(title, description) for a golden segment. VLM when keyed, placeholder otherwise."""
    if not vlm.available:
        return (f"Step {i + 1} {_FILL}", "")
    # TODO(stage-3 agent): show the segment's keyframes and ask for a concise imperative
    # title + 1-2 sentence description of the action performed. Fall back to placeholder on error.
    return (f"Step {i + 1} {_FILL}", "")


def render_markdown(procedure: Procedure) -> str:
    """Human-readable procedure.md."""
    mins = procedure.total_est_seconds / 60.0
    lines = [
        f"# {procedure.title}",
        "",
        f"_Source: `{procedure.source_video}` — estimated total "
        f"{procedure.total_est_seconds:.0f}s (~{mins:.1f} min), {len(procedure.steps)} steps._",
        "",
    ]
    for s in procedure.steps:
        lines.append(f"## {s.index + 1}. {s.title}")
        lines.append(f"_{s.start_t:.1f}s–{s.end_t:.1f}s · est {s.est_seconds:.0f}s_")
        if s.description:
            lines.append("")
            lines.append(s.description)
        if s.intent:
            lines.append("")
            lines.append(f"**Intent:** {s.intent}")
        lines.append("")
    return "\n".join(lines)
