# procap

procap turns a screenshare recording of a technical GUI (operating a machine, a lab
instrument, a control panel) into a written procedure. It decomposes the video into
keyframes at moments of meaningful change, classifies stretches as **golden**
(consequential actions worth keeping) or **dross** (mis-clicks reverted, mouse
wander, dead time), then synthesizes an ordered, time-estimated procedure from the
golden stretches — and can audit that procedure against an existing written doc to
flag gaps. Heuristics are the always-on baseline; a vision LLM enriches the output
when an `ANTHROPIC_API_KEY` is present (`procap.vlm`), never as a hard dependency.

## Map

- `procap/model.py` — the frozen data contracts (`Keyframe`, `Segment`, `Procedure`,
  `AuditReport`) every stage reads/writes. Change here ripples everywhere — treat as an interface.
- `procap/run.py` — the on-disk artifact manager: a run dir holds `keyframes.json`,
  `segments.json`, `procedure.json`, and the extracted frames. Stages hand off via these files.
- `procap/extract.py` — stage 1: video → keyframes (ffmpeg sample + perceptual-hash/SSIM diff).
- `procap/golden.py` — stage 2: keyframes → golden/dross segments (revert-detection heuristic + optional VLM).
- `procap/procedure.py` — stage 3: golden segments → time-estimated procedure (+ manual intent fill-in).
- `procap/audit.py` — stage 3: compare a generated procedure against a written doc.
- `procap/vlm.py` — the vision-LLM client wrapper; degrades to a deterministic offline stub with no key.
- `procap/cli.py` — `procap extract|golden|procedure|audit|run`.
- `corpus/` — test videos: `synthetic/` (generated, ground-truth-labeled, committed) + real (gitignored).
- `docs/decisions/` — design calls + why. `road.md` — phase roadmap & live verdicts.

## Context hygiene

- Grep for symbols, fields, constants, and call sites before reading any file.
- Structure-scan before any markdown range read: `grep -n "^##" <file>.md` first, then bounded reads.
- Reads over ~150 lines require a stated reason; prefer one complete function/class range.
- Constrain repo-wide greps to source extensions (e.g. `--include="*.py"`).

## Working here

- Python lives in the venv: `.venv/bin/python`, `.venv/bin/pytest`. ffmpeg/ffprobe are on PATH (`~/.local/bin`).
- Validation: `.venv/bin/python -m pytest -q` and `.venv/bin/python -m procap.cli run corpus/synthetic/labeled_demo.mp4`.
- Pre-commit linter: ERROR blocks, WARN advises (`tools/lint.py`). Arm: `git config core.hooksPath .githooks`.
- **Never codify silently.** Rule/convention changes are proposed and accepted before writing — use `/codify`.
- **Design calls leave a record** in `docs/decisions/` (call + why), frontmatter `status: decided|superseded`.
- Wrapping up mid-task: `/handoff` writes a decision-ready handoff to `handoffs/`.

## Engineering defaults

Fail loud, not silently — surface unexpected state; keep error handling narrow with diagnostic
context in the message; use enums/constants for bounded value sets (see `SegmentKind`); names
communicate purpose; extract for clarity but don't abstract ahead of real reuse. The VLM is
optional everywhere: any code path that calls it must have a heuristic/offline fallback.

## Execution discipline

- **Disagreement and pushback**: never silently execute an approach believed mistaken. Raise at
  planning time, calibrated by consequence.
- **After two failed attempts**: stop and diagnose the root cause before a third. Name whether a
  fix addresses the root cause or only a symptom.
- **Review before committing**: when a durable chunk is done, review end-to-end before committing.
- **Verify load-bearing claims** structurally (shared symbols / call paths), never nominally.

## Subagent discipline

For non-trivial Agent/spawn calls, end the prompt with: "End your response with a `## Takeaways`
block: decisions made, gotchas hit, lessons worth keeping." Subagent transcripts aren't visible to
the parent — only the return value is.

## Context routing

Durable knowledge goes to the machinery, not harness auto-memory: in-flight resumable context →
`/handoff`; findable history → `/wrap-session`; rule/convention → `/codify`; design call + why →
`docs/decisions/`.
