---
project: procap
goal: Carry procap forward — 3-stage pipeline is built & tested; frontier is live-VLM validation + golden over-segmentation
created: 2026-06-26
status: open
---

# Goal

procap turns a screenshare of a technical GUI into a time-estimated written procedure
(extract → golden → procedure → audit). The full pipeline is **built and tested offline**.
The next session's job is to validate the VLM enrichment layer *live* (with a real API key)
and tackle the one known quality gap: golden over-segmentation on real video.

# State

**Done AND verified (run, output inspected):**
- Repo created at `~/projects/procap`, wired to CMS (skills, linter, pre-commit hooks,
  monition joined to the brain2 hub, identity docs). Clean `master` @ `8aa0aa6`.
- Env: project `.venv` (all deps), static `ffmpeg`/`ffprobe` in `~/.local/bin`.
- **Stage 1 (extract)** — fully implemented & tuned against *measured* changed-fraction data.
  Cursor-wander correctly produces no keyframe. Verified on synthetic (6 keyframes) and a real
  3-min KiCad clip (60 keyframes, sensible).
- **Stage 2 (golden)** — revert-detection heuristic; on the labeled synthetic clip it recovers
  exactly the 4 golden actions and rejects wander + wrong-tab-then-revert. F1 test passes.
- **Stage 3 (procedure + audit)** — timestamped steps with `[fill in]` slots; offline audit
  flags the step a gappy written doc omits (75% coverage, correct). Verified end-to-end.
- **33 tests pass** (`.venv/bin/python -m pytest -q`). Real-corpus findings in
  `docs/decisions/2026-06-26-real-corpus-findings.md`: default thresholds generalize unchanged.

**Done but NOT verified live (offline-tested only — no API key this session):**
- VLM enrichment across all three stages (`golden.refine_with_vlm`, `procedure._describe`,
  `audit._audit_semantic`). Wired, guarded by `VLM.available`, robust JSON parsing with
  heuristic/placeholder fallback, unit-tested with a mocked `VLM.ask` (`tests/test_vlm.py`).
  **Never run against the real Anthropic API.** CLI now threads keyframes through so the path
  is reachable once a key exists (`8aa0aa6`).

# Next actions (ordered)

1. **Live-validate the VLM layer.** Set `ANTHROPIC_API_KEY`, run
   `.venv/bin/python -m procap.cli run corpus/synthetic/labeled_demo.mp4 --against corpus/synthetic/written_procedure.md`,
   and inspect `runs/labeled_demo/procedure.md` (should now have real titles/descriptions, not
   `[fill in]`) and `audit.md` (semantic alignment path). Confirm `extract_json` survives real
   replies (model wrapping JSON in prose/markdown fences is the likely failure — see vlm.py).
2. **Attack golden over-segmentation** (the real frontier; `debt.md` row on `golden.py:classify`).
   29 golden "steps" for a 3-min KiCad clip because each visual change (pan/zoom/panel-toggle)
   becomes a step. This is semantic, NOT a threshold fix. Approach: have the VLM *merge/relabel*
   consecutive keyframes that belong to one logical action (or mark incidental view-changes as
   dross). Re-measure step count on the KiCad clip after.
3. Optionally generate a second, richer synthetic clip (multi-action sequences) to test the
   merge logic with known-good labels.

# Key context

- **Architecture contract** (read first): `docs/decisions/2026-06-26-pipeline-and-contracts.md` —
  stages hand off via JSON artifacts in a run dir (`procap/run.py`); `procap/model.py` is the
  frozen data contract; **heuristics are the always-on baseline, the VLM only enriches**. Any
  VLM code path MUST have a heuristic fallback.
- **The change metric is changed-pixel-fraction, NOT SSIM** (`procap/imageutil.py`). SSIM was
  tried first and is blind to small high-contrast UI text changes on flat screen UIs — switched
  after measuring. `change_threshold=0.0025` sits in a measured bimodal valley; `min_dwell_s=0.6`
  is the governing knob for keyframe count. Don't retune without the sweep in the findings doc.
- **Testing is split deliberately:** synthetic clip (`corpus/make_synthetic.py`, gitignored .mp4,
  committed `.labels.json`) gives ground-truth correctness; `corpus/fetch_real.py` pulls a real
  KiCad screencast for robustness (`corpus/real/`, gitignored). `procap/eval.py` scores golden F1.
- **Gotchas hit:** (a) no `sudo`/TTY → static ffmpeg, not apt. (b) PEP-668 → venv, not system pip.
  (c) `yt-dlp` invoked as `python -m yt_dlp` (not on PATH). (d) **agent-spawn into a repo with
  `.mcp.json`**: unattended worktree agents halt on folder-trust (Enter) THEN MCP-trust (Down Down
  Enter = "continue without"); a too-long `--prompt` truncated the `--mcp-config` arg and silently
  killed a spawn (looked alive in `tmux ls`, was dead bash). Use a short prompt pointing at a
  committed prompt file.

# Open decision

**How aggressively should the VLM collapse over-segmented steps?** Options: (a) conservative —
VLM only relabels clearly-incidental view changes (pan/zoom) as dross, keeps anything ambiguous
golden; (b) aggressive — VLM groups consecutive keyframes into one logical action. Recommendation:
**(a) first** — it's lower-risk, preserves the "fail toward keeping" bias, and is measurable
against the synthetic labels before trusting it on real video. Decide once you see live VLM
behavior from Next-action 1.

# Pointers

- Roadmap: `road.md` (Phase 1 done; Phases 2–3 marked in progress → update to reflect completion).
- Design calls: `docs/decisions/2026-06-26-pipeline-and-contracts.md`,
  `docs/decisions/2026-06-26-real-corpus-findings.md`.
- Debt: `debt.md` (over-segmentation row is the priority).
- Agent task prompts (what the two spawned agents were told): `handoffs/agent-{vlm,corpus}-prompt.md`.
