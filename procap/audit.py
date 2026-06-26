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
from .vlm import VLM, extract_json

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
        return _audit_semantic(procedure, doc_steps, path, vlm)

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


_ALIGN_PROMPT = (
    "You are auditing whether a written procedure covers an action captured on video.\n\n"
    "CAPTURED STEP (from the video):\n"
    "  title: {title}\n"
    "  description: {desc}\n\n"
    "WRITTEN-DOC STEPS (numbered):\n{doc_list}\n\n"
    "Which written-doc step (if any) describes the SAME action as the captured step? "
    "Judge by meaning, not wording or position.\n"
    'Reply with ONLY a JSON object: {{"match": <doc step number, or 0 if none describes '
    'this action>, "under_documented": <true if the matched doc step mentions the action '
    "only thinly/incompletely, else false>}}."
)


def _audit_semantic(procedure: Procedure, doc_steps: list[str], path: Path,
                    vlm: VLM) -> AuditReport:
    """VLM-aligned audit: match each generated step to the doc step describing the same action.

    Falls back to treating a step as unmatched (missing_step) on any VLM/parse error, so a
    flaky model degrades gracefully rather than crashing the audit.
    """
    findings: list[AuditFinding] = []
    n_proc = len(procedure.steps)
    doc_list = "\n".join(f"  {j + 1}. {t}" for j, t in enumerate(doc_steps)) or "  (none)"

    matched_docs: set[int] = set()
    max_matched = 0          # highest doc index matched so far (1-based)
    covered = 0
    for step in procedure.steps:
        prompt = _ALIGN_PROMPT.format(
            title=step.title, desc=step.description or "(no description)", doc_list=doc_list)
        try:
            data = extract_json(vlm.ask(prompt))
            match = int(data.get("match", 0))
            under = bool(data.get("under_documented", False))
        except Exception:
            match, under = 0, False  # treat as unmatched -> flagged missing below

        if not (1 <= match <= len(doc_steps)):
            findings.append(AuditFinding(
                kind=FindingKind.MISSING_STEP.value,
                detail=f"generated step {step.index + 1} ('{step.title}') has no "
                       f"counterpart in the written doc",
                procedure_step_index=step.index,
            ))
            continue

        covered += 1
        matched_docs.add(match)
        doc_ref = doc_steps[match - 1]
        if match < max_matched:
            findings.append(AuditFinding(
                kind=FindingKind.OUT_OF_ORDER.value,
                detail=f"generated step {step.index + 1} ('{step.title}') matches written "
                       f"doc step {match} ('{doc_ref}'), which is out of sequence",
                procedure_step_index=step.index,
                doc_ref=doc_ref,
            ))
        else:
            max_matched = match
        if under:
            findings.append(AuditFinding(
                kind=FindingKind.UNDER_DOCUMENTED.value,
                detail=f"generated step {step.index + 1} ('{step.title}') is matched by "
                       f"written doc step {match} ('{doc_ref}') but only thinly",
                procedure_step_index=step.index,
                doc_ref=doc_ref,
            ))

    # Doc steps no generated step matched describe actions the video never shows.
    for j, t in enumerate(doc_steps):
        if (j + 1) not in matched_docs:
            findings.append(AuditFinding(
                kind=FindingKind.EXTRA_IN_DOC.value,
                detail=f"written doc step {j + 1} ('{t}') is not seen in the video",
                doc_ref=t,
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
