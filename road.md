# procap Roadmap

## 1. How to use this doc

Purpose: orient sessions around project phase, history, next work, and bounded
implementation surfaces.

Current phase marker: `<----- Ongoing phase ----->`

A **phase** is a high-level deliverable with stable interfaces. A **workstream** is a
refinable effort inside a phase. A **bucket** is a bounded implementation slice.

---

## 2. Phase roadmap

### Phase 1 — Decompose (video → keyframes)

**Status:** complete (verified on synthetic + a real 3-min KiCad clip).

**Deliverable:** `procap extract VIDEO` samples the video and emits ordered keyframes at
moments of durable visual change, with timestamps, to a run dir.

**Surfaces:** `procap/extract.py`, `procap/model.py:Keyframe`, `procap/run.py`.

**Design:** ffmpeg samples at a fixed fps; consecutive frames are diffed by perceptual
hash (coarse) + SSIM (fine); a keyframe is emitted when change exceeds a threshold and the
new state then *dwells* (filters transient mid-transition frames). See
`docs/decisions/2026-06-26-pipeline-and-contracts.md`.

**Validation:** `pytest tests/test_extract.py`; `procap extract corpus/synthetic/labeled_demo.mp4`.

**Exit:** keyframes recovered on the synthetic clip match the known state-change count ±1.

---

### Phase 2 — Golden / dross classification

**Status:** complete (heuristic baseline; F1 ≥ 0.8 on synthetic). VLM re-judging wired,
offline-tested only — live validation tracked in Phase 4.

**Deliverable:** `procap golden RUN` partitions the keyframe timeline into golden vs dross
segments with a reason per segment.

**Surfaces:** `procap/golden.py`, `procap/model.py:Segment,SegmentKind`.

**Design:** heuristic baseline — (a) **revert-detection**: if the state at keyframe *j*
returns (low changed-pixel fraction, `imageutil.changed_fraction_paths`) to an earlier
state at *i<j*, the excursion *i+1..j* is dross; (b) **dwell**: states held < `min_dwell_s`
are transient/dross. The VLM, when keyed, re-judges ambiguous segments. Heuristic must stand alone.
- **(c) idle / wander dross — PLANNED, not yet implemented** (Phase 2 follow-up): change
  confined to the cursor with no UI delta, and long *non-reverting* idle holds, are a
  structural blind spot of revert-detection. The `labeled_demo` eval overlay shows the cost
  (the 4–7s wander scores as a false-positive golden, F1 0.84 not 1.0). Until built,
  idle/dead-time dross is undetected (corrected 2026-06-26 via confer w/ brain2).

**Validation:** `pytest tests/test_golden.py` against the synthetic clip's ground-truth
labels (it contains a scripted wrong-click-and-revert excursion + a mouse-wander stretch —
the wander is currently a known miss, see (c)).

**Exit:** on the synthetic clip, golden/dross labels match ground truth with ≥ 0.8 F1.
(Met: F1 0.84, recall 1.0, precision 0.72 — the precision gap is the undetected wander.)

---

### Phase 3 — Procedure synthesis + audit

**Status:** complete (offline skeleton + audit verified end-to-end). VLM titling/description
and semantic audit wired, offline-tested only — live validation tracked in Phase 4.

**Deliverable:** `procap procedure RUN` emits a time-estimated, ordered procedure (one step
per golden segment, timestamps → duration estimates, manual `intent` fill-in slots), and
`procap audit RUN --against DOC.md` compares it to a written procedure. **With placeholder
titles the audit is a structural step-count alignment** (flags missing / extra by
position+count only). **Out-of-order / under-documented detection is content-dependent, not
VLM-only**: it keys on step titles/intents, which can come from the VLM *or* a manual
`intent` fill-in — so an offline text-match audit on filled intents is reachable without a
model (not yet built). It's content-gated, not VLM-gated (clarified 2026-06-26 via confer
w/ brain2; corrected from an earlier "requires the VLM" overclaim).

**Surfaces:** `procap/procedure.py`, `procap/audit.py`,
`procap/model.py:Procedure,ProcedureStep,AuditReport,AuditFinding`.

**Design:** offline mode produces a structural skeleton (titles `[fill in]`, real durations
from timestamps); VLM mode fills titles/descriptions from the keyframes and does semantic
audit alignment. Time estimate = segment wall-time, optionally adjusted by a per-step
complexity factor.

**Validation:** `pytest tests/test_procedure.py`, `tests/test_audit.py`; end-to-end
`procap run` on the synthetic clip; audit against a deliberately-gappy written doc recovers
the omitted step.

**Exit:** end-to-end `procap run` produces a `procedure.md` whose step count equals the
golden-segment count, and the audit flags the known gap in the sample written doc.

---

### Phase 4 — VLM enrichment hardening

`<----- Ongoing phase ----->`

**Status:** active (frontier; baselines done, this is the quality layer).

**Deliverable:** the VLM enrichment paths validated *live* against the real Anthropic API,
and golden over-segmentation reduced so a real recording yields logical-action steps rather
than one step per visual change.

**Surfaces:** `procap/golden.py:refine_with_vlm`, `procap/procedure.py:_describe`,
`procap/audit.py:_audit_semantic`, `procap/vlm.py:extract_json`.

**Design:** (a) **live-validate** with `ANTHROPIC_API_KEY` set — confirm `extract_json`
survives real replies (models wrap JSON in prose/fences) and titles/semantic-audit are sound;
(b) **de-segment** — have the VLM merge/relabel consecutive keyframes belonging to one logical
action (or mark incidental pan/zoom/panel-toggle as dross). Start conservative (relabel only
clearly-incidental view changes) before grouping; measure step count on the KiCad clip.
This is semantic, **not** a threshold change (see `docs/decisions/2026-06-26-real-corpus-findings.md`
and the `golden.py:classify` row in `debt.md`).

**Validation:** live run on `corpus/synthetic/labeled_demo.mp4` (real titles, not `[fill in]`);
re-measure golden step count on the real KiCad clip before/after de-segmentation.

**Estimate:** medium.

**Exit:** with a key, `procap run` produces real step titles/descriptions and a semantic audit;
the KiCad clip's procedure collapses from ~29 steps to a count matching its logical actions.

---

### Deferred follow-ups (from 2026-06-26 confer w/ brain2 — recorded, not scheduled)

Surfaced while hardening the demo + honesty pass (see
`docs/decisions/2026-06-26-demo-and-honesty-pass.md`). Captured here so they aren't relitigated:

- **Idle / no-UI-delta dross signal (Phase 2).** Revert-detection is structurally blind to
  *non-reverting* dross (idle, dead time, slow wander); on `labeled_demo` the 4–7s wander
  scores as a false-positive golden (precision 0.72). The `procedure` stage now *accounts* for
  it honestly (held-time decomposition) rather than mislabelling — a real classify-layer
  signal is deferred. Note: extract.py *fuses* idle into the preceding keyframe's dwell window,
  so this likely belongs at the extract/de-segmentation layer (Phase 4), not `golden.classify`.
- **Out-of-sample F1 (Phase 2).** Accuracy is measured on the *one* labeled clip — in-sample.
  The fix is a **second labeled synthetic clip** to report held-out F1; the demo currently
  states the in-sample caveat explicitly.
- **Offline `under_documented` audit.** Left VLM-only — sentence length is a junk proxy for
  thinness. The offline lexical audit ships missing / extra / out-of-order only.
- **Nice-to-have demo:** multi-run comparison view; offline diff-region hint (where on screen
  changed); a `max_active_s` / `match_floor` mini-sweep readout.
