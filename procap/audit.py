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

from .model import Procedure, AuditReport, AuditFinding, FindingKind, AuditMethod
from .vlm import VLM, extract_json

# Offline content match: a generated step matches the doc step with the highest word-overlap
# (Jaccard) above this floor. A tuned, surfaced knob — NOT semantics. Below the floor a step
# is "missing." Kept conservative so shared verbs ("click", "open") alone don't force a match.
DEFAULT_MATCH_FLOOR = 0.12

# Tiny stopword set so generic procedure vocabulary doesn't dominate the overlap. Not
# linguistics — just the high-frequency glue that would otherwise match everything.
_STOPWORDS = frozenset(
    "the a an to of and or is are be in on at for with from this that it its as you your "
    "click open set start stop confirm step the then press select enter".split()
)

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


def _step_text(step) -> str:
    """The content a step offers for matching: the manual intent + a non-placeholder title +
    description. Placeholder `[fill in]` titles carry no content, so they're excluded."""
    parts = [step.intent or "", step.description or ""]
    if "[fill in" not in step.title:
        parts.append(step.title)
    return " ".join(parts)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _has_content(procedure: Procedure) -> bool:
    """True once steps carry real content (filled intent or a non-placeholder title) — the
    precondition for a content match. Offline with bare placeholders this is False."""
    return any(s.intent.strip() or "[fill in" not in s.title for s in procedure.steps)


def audit(procedure: Procedure, written_doc: str | Path, vlm: VLM | None = None,
          match_floor: float = DEFAULT_MATCH_FLOOR) -> AuditReport:
    """Compare `procedure` against the written doc at `written_doc`.

    Three alignment methods, picked by what's available — each bounds what can be found:
    VLM semantic > offline lexical (when steps have content) > positional count baseline.
    """
    vlm = vlm or VLM()
    path = Path(written_doc)
    doc_text = path.read_text()
    doc_steps = parse_written_steps(doc_text)

    if vlm.available:
        return _audit_semantic(procedure, doc_steps, path, vlm)
    if _has_content(procedure):
        return _audit_lexical(procedure, doc_steps, path, match_floor)

    findings: list[AuditFinding] = []
    n_proc = len(procedure.steps)
    n_doc = len(doc_steps)

    # Positional baseline: with placeholder titles there is no content to match, so we can
    # only align by count/position — flags count mismatches, NOT order or content gaps.
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
    return AuditReport(written_doc=str(path), coverage=round(coverage, 3),
                       method=AuditMethod.COUNT.value, findings=findings)


def _audit_lexical(procedure: Procedure, doc_steps: list[str], path: Path,
                   match_floor: float) -> AuditReport:
    """Offline content audit by word-overlap — no model. Matches each generated step to the
    doc step with highest Jaccard overlap above `match_floor`, THEN checks order on the
    matched indexes. This is lexical, not semantic: it can mis-pair on shared vocabulary, and
    `under_documented` (a thinness judgement) is deliberately left to the VLM path."""
    findings: list[AuditFinding] = []
    n_proc = len(procedure.steps)
    doc_tok = [_tokens(t) for t in doc_steps]

    matched_docs: set[int] = set()
    max_matched = 0          # highest doc index matched so far (1-based) — for order check
    covered = 0
    for step in procedure.steps:
        st = _tokens(_step_text(step))
        best_j, best_score = 0, 0.0
        for j, dt in enumerate(doc_tok):
            score = _jaccard(st, dt)
            if score > best_score:
                best_j, best_score = j + 1, score   # 1-based

        if best_score < match_floor or best_j == 0:
            findings.append(AuditFinding(
                kind=FindingKind.MISSING_STEP.value,
                detail=f"generated step {step.index + 1} ('{step.title}') has no "
                       f"counterpart in the written doc (best lexical overlap "
                       f"{best_score:.2f} < floor {match_floor:.2f})",
                procedure_step_index=step.index,
            ))
            continue

        covered += 1
        matched_docs.add(best_j)
        doc_ref = doc_steps[best_j - 1]
        if best_j < max_matched:
            findings.append(AuditFinding(
                kind=FindingKind.OUT_OF_ORDER.value,
                detail=f"generated step {step.index + 1} ('{step.title}') matches written "
                       f"doc step {best_j} ('{doc_ref}'), which is out of sequence",
                procedure_step_index=step.index,
                doc_ref=doc_ref,
            ))
        else:
            max_matched = best_j

    for j, t in enumerate(doc_steps):
        if (j + 1) not in matched_docs:
            findings.append(AuditFinding(
                kind=FindingKind.EXTRA_IN_DOC.value,
                detail=f"written doc step {j + 1} ('{t}') is not seen in the video",
                doc_ref=t,
            ))

    coverage = (covered / n_proc) if n_proc else 1.0
    return AuditReport(written_doc=str(path), coverage=round(coverage, 3),
                       method=AuditMethod.LEXICAL.value, findings=findings)


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
    return AuditReport(written_doc=str(path), coverage=round(coverage, 3),
                       method=AuditMethod.VLM.value, findings=findings)


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
