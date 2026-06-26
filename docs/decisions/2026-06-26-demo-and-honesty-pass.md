---
doctype: decision
status: decided
date: 2026-06-26
---
# Local web demo + a three-cycle honesty pass on the pipeline

## Decision

Build a dependency-free local web demo of the procap pipeline, and use a three-cycle
adversarial confer with the brain2 architectural adviser to drive an **honesty pass**: make
the demo and the docs *expose the pipeline's seams* rather than flatter them, and make every
load-bearing capability claim true **in code**, not just nominally.

Provenance: confer thread
`brain2/handoffs/archive/2026-06-26-confer-procap-demo-and-work-critique.md` (3 cycles).

### What shipped

**Demo (`procap/webdemo.py`, `procap serve`)** — stdlib `http.server`, zero new deps
(deliberate, matching the minimal-deps / heuristics-always-on ethos). Reads run artifacts;
adds no logic of its own. Renders per run: keyframe filmstrip, golden/dross timeline, an
**eval overlay** (truth-vs-predicted strips + measured P/R/F1 for any run with a sibling
`.labels.json`), procedure steps, and a method-labelled audit section.

**Honesty fixes (the substance):**
1. **Eval overlay** — the page now *scores itself* on the labeled clip
   (precision 0.72 / recall 1.0 / F1 0.84) instead of asserting the golden/dross call works.
2. **Wander overclaim struck** — the decision doc / roadmap claimed the heuristic baseline
   detects mouse-wander; `golden.classify` only does revert-detection + min-dwell. Corrected
   to *planned*; the overlay shows the cost (the 4–7s wander is a false-positive golden).
3. **Time-estimate dwell accounting** (`ProcedureStep.held_seconds`, `procedure.py`,
   `DEFAULT_MAX_ACTIVE_S`) — a stretch held past the active cap reports
   `≤X active + Y held (attribution unknown)` rather than asserting the whole span as action
   time. **Recall-safe**: decomposition only, never drops a step. This replaced a *rejected*
   idle-dross classifier that would have regressed recall 1.0→0.75 for +0.02 F1 (a procedure
   tool must not silently delete steps).
4. **Audit honesty + method field** (`AuditReport.method` ∈ {count, lexical, vlm}). The
   offline path is no longer a green "%coverage" costume over a count ratio; the Overview
   shows a matched-step **count**, and §4 is labelled by method.
5. **Offline lexical content audit** (`audit._audit_lexical`) — proves the
   *content-gated, not VLM-gated* claim in code: with manually-filled intents and no API key,
   a word-overlap match + order check recovers missing / out-of-order / extra. It is
   **lexical, not semantic** (can mis-pair on shared terms; labelled as such), and
   `under_documented` is deliberately left to the VLM. Demonstrated live by `runs/audit_demo`
   (built reproducibly by `corpus/make_audit_demo.py`) and asserted by `tests/test_audit.py`.
6. **Threshold honesty** — the demo surfaces the knobs from `meta.json`
   (`change_threshold`, `min_dwell_s`, `max_active_s`, `match_floor`) and states the
   **in-sample / train-on-test** caveat (accuracy is measured on the one clip thresholds were
   chosen against).

## Why

- **Why a confer, three cycles:** an independent adversarial reviewer caught what a
  self-review missed — most sharply, that the demo *actively asserted* false things (a count
  ratio dressed as a content audit; a "VLM-enriched" masthead on a 100%-heuristic run) and
  that a design-doc capability claim diverged from the call graph. Each was found by tracing a
  claim **structurally** (call paths, the field a signal keys on) rather than nominally.
- **Why dwell-accounting over an idle classifier:** `extract.py` opens a keyframe only on a
  change-threshold crossing, so idle is *absorbed into the prior keyframe's dwell window* and
  never forms its own near-zero-change keyframe. An idle predicate keyed on `change_score`
  therefore fires mostly where revert already fires, and where it adds a verdict it nukes the
  fused action+idle frame — dropping a real step. The harm (contaminated time estimate) lives
  in the *estimate*, so it is fixed there, honestly and recall-safely.
- **Why lexical audit offline:** "out-of-order" is structurally meaningless under positional
  1:1 alignment; it only becomes real once steps are matched by **content**. Content can come
  from the VLM *or* a human fill-in, so the capability is content-gated, not VLM-gated —
  attributing it to the VLM was the nominal-vs-structural trap.

## Consequences

- `procap/model.py` gains two backward-compatible optional fields (`ProcedureStep.held_seconds`,
  `AuditReport.method`); old artifacts still load.
- Tests grew 33 → 37 (held-accounting recall-safety; offline lexical audit fires
  missing/out-of-order/extra; placeholder titles stay on the count baseline).
- Deferred items (idle-dross signal at the extract layer; a second labeled clip for
  out-of-sample F1; offline `under_documented`; multi-run / diff-region demo views) are
  recorded in `road.md` under "Deferred follow-ups," not scheduled.
