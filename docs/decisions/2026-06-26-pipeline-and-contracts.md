---
doctype: decision
status: decided
date: 2026-06-26
---
# Pipeline shape, file-based contracts, and offline-first VLM

## Decision

**Three stages, joined by on-disk JSON artifacts in a run dir, not by in-memory calls.**
`extract → golden → procedure/audit`. Each stage reads the previous stage's artifact file
and writes its own. The data contracts (`procap/model.py`) are the interface; the run dir
(`procap/run.py`) is the transport.

**Heuristics are the always-on baseline; the VLM is an enhancement layer.** Every stage
produces a real, useful result with no API key. The vision LLM (`procap/vlm.py`, Claude
Opus 4.8) only *refines*: re-judging ambiguous golden/dross segments, and writing step
titles/descriptions + semantic audit alignment. With no `ANTHROPIC_API_KEY`, `vlm.available`
is false and stages take the heuristic path.

## Why

- **File-based handoff** lets the three stages be built, tested, and re-run independently
  (re-run `golden` without re-extracting; develop `procedure` against a hand-authored
  `segments.json`). It also made parallel development across isolated git worktrees possible
  without a shared in-memory surface to collide on.
- **Offline-first** because (a) this environment has no API key, so a VLM-hard-dependency
  pipeline would be untestable here, and (b) a procedure tool that dies without network/credits
  is fragile for the lab/operations setting it targets. Heuristics give a deterministic,
  unit-testable spine; the VLM is upside, not a single point of failure.
- **Golden detection has a genuine non-VLM core**: a mis-click-and-return shows up as a
  perceptual-hash *return* to a prior state. That much is implemented and measurable without
  semantics, so the baseline is real, not a stub.
  - **Correction (2026-06-26, confer w/ brain2):** an earlier version of this line also
    claimed the baseline detects "mouse-wander as change confined to the cursor with no UI
    delta." That capability is **not implemented** — `golden.classify` does only
    revert-detection + a min-dwell flicker filter; there is no cursor/UI-delta or idle-dross
    signal. The eval overlay on `labeled_demo` proves the miss (the 4–7s wander is scored as a
    false-positive golden, contaminating step 2's time estimate). Non-reverting dross (idle,
    dead time, slow wander) is a structural blind spot of revert-detection. Treat
    idle/no-UI-delta dross as **planned**, not delivered (see `road.md` Phase 2 follow-up).

## Consequences

- Testing splits cleanly: a **synthetic, ground-truth-labeled** clip validates heuristic
  correctness (we script the wrong-click-and-revert and the wander, so we know the labels); a
  **real downloaded** screencast validates robustness. VLM paths are unit-tested with mocked
  responses and skipped live when unkeyed.
- `procedure.md` in offline mode has real timestamps/durations but `[fill in]` titles — which
  doubles as the spec's "inviting manual filling out of what you're doing." That's a feature.
- Long videos re-decode wastefully (see `debt.md`); acceptable until a real long-video case lands.
