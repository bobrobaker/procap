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

**Status:** done (foundation).

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

`<----- Ongoing phase ----->`

**Status:** in progress (spawned agent).

**Deliverable:** `procap golden RUN` partitions the keyframe timeline into golden vs dross
segments with a reason per segment.

**Surfaces:** `procap/golden.py`, `procap/model.py:Segment,SegmentKind`.

**Design:** heuristic baseline — (a) **revert-detection**: if the state at keyframe *i*
returns (low perceptual-hash distance) to a pre-excursion state at *j<i*, the excursion
*j+1..i* is dross; (b) **dwell**: states held < `min_dwell_s` are transient/dross;
(c) **wander**: change confined to cursor region with no UI delta is dross. The VLM, when
keyed, re-judges ambiguous segments. Heuristic must stand alone.

**Validation:** `pytest tests/test_golden.py` against the synthetic clip's ground-truth
labels (it contains a scripted wrong-click-and-revert excursion + a mouse-wander stretch).

**Exit:** on the synthetic clip, golden/dross labels match ground truth with ≥ 0.8 F1.

---

### Phase 3 — Procedure synthesis + audit

**Status:** in progress (spawned agent).

**Deliverable:** `procap procedure RUN` emits a time-estimated, ordered procedure (one step
per golden segment, timestamps → duration estimates, manual `intent` fill-in slots), and
`procap audit RUN --against DOC.md` compares it to a written procedure, flagging missing /
out-of-order / under-documented steps.

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
