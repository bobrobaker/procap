"""Stage 3b — audit a generated procedure against an existing written procedure.

The written doc typically has fewer screenshots and is terser than what the video shows;
the audit's job is to flag where the doc *under-covers* the captured reality: steps the
doc omits, steps out of order, or steps the doc mentions only thinly.

Baseline (offline): parse the written doc into steps (markdown headings or numbered/bulleted
lines), align by sequence/count, and report coverage + missing-step findings. With a VLM,
alignment becomes semantic (match a generated step to the doc step that describes the same
action, by content not position). The baseline must stand alone.
"""
from __future__ import annotations

import re
from pathlib import Path

from .model import Procedure, AuditReport, AuditFinding, FindingKind
from .vlm import VLM

_STEP_PATTERNS = [
    re.compile(r"^\s{0,3}#{2,6}\s+(.*\S)\s*$"),          # markdown heading H2+ (H1 is the doc title)
    re.compile(r"^\s*\d+[.)]\s+(.*\S)\s*$"),             # 1. / 1) numbered
    re.compile(r"^\s*[-*+]\s+(.*\S)\s*$"),               # - bullet
]


def parse_written_steps(doc_text: str) -> list[str]:
    """Extract an ordered list of step titles from a written procedure (markdown/plain)."""
    steps: list[str] = []
    for line in doc_text.splitlines():
        for pat in _STEP_PATTERNS:
            m = pat.match(line)
            if m:
                steps.append(m.group(1).strip())
                break
    return steps


def audit(procedure: Procedure, written_doc: str | Path, vlm: VLM | None = None) -> AuditReport:
    """Compare `procedure` against the written doc at `written_doc`."""
    vlm = vlm or VLM()
    path = Path(written_doc)
    doc_text = path.read_text()
    doc_steps = parse_written_steps(doc_text)

    if vlm.available:
        # TODO(stage-3 agent): semantic alignment — for each generated step, find the doc
        # step describing the same action (or none -> missing_step). Replace the positional
        # baseline below. Keep this function's signature + return type.
        pass

    findings: list[AuditFinding] = []
    n_proc = len(procedure.steps)
    n_doc = len(doc_steps)

    # Positional baseline: the doc is expected to cover the generated steps in order.
    covered = min(n_proc, n_doc)
    for i in range(covered, n_proc):
        findings.append(AuditFinding(
            kind=FindingKind.MISSING_STEP.value,
            detail=f"generated step {i + 1} ('{procedure.steps[i].title}') has no "
                   f"counterpart in the written doc",
            procedure_step_index=i,
        ))
    for j in range(covered, n_doc):
        findings.append(AuditFinding(
            kind=FindingKind.EXTRA_IN_DOC.value,
            detail=f"written doc step {j + 1} ('{doc_steps[j]}') is not seen in the video",
            doc_ref=doc_steps[j],
        ))

    coverage = (covered / n_proc) if n_proc else 1.0
    return AuditReport(written_doc=str(path), coverage=round(coverage, 3), findings=findings)


def render_markdown(report: AuditReport) -> str:
    lines = [
        "# Procedure audit",
        "",
        f"_Against `{report.written_doc}` — coverage {report.coverage * 100:.0f}%, "
        f"{len(report.findings)} finding(s)._",
        "",
    ]
    if not report.findings:
        lines.append("No gaps found: the written doc covers every captured step.")
    for f in report.findings:
        ref = f" (doc: {f.doc_ref})" if f.doc_ref else ""
        lines.append(f"- **{f.kind}**{ref}: {f.detail}")
    lines.append("")
    return "\n".join(lines)
