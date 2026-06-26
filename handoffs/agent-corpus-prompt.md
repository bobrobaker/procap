You are an autonomous implementer working in a git worktree of the `procap` project. Read
`CLAUDE.md`, `README.md`, `road.md`, and `docs/decisions/2026-06-26-pipeline-and-contracts.md`
first. procap turns a screenshare of a technical GUI into a time-estimated written procedure
via 4 stages: extract → golden → procedure → audit. Stage 1 is fully implemented; stages 2–3
have working heuristic baselines validated on a synthetic, ground-truth-labeled clip.

## Your job: validate robustness on a REAL screencast, and harden the test surface

The synthetic clip proves correctness on known labels. Your job is to prove the pipeline
generalizes to a real screen recording of a technical GUI, and to write the integration
tests + a findings report.

1. NEW `corpus/fetch_real.py` — a small script that uses `yt-dlp` (already installed in the
   venv) to download ONE short (≤ ~3 min) **screen-only** recording of someone operating a
   technical/engineering GUI (examples of good subjects: oscilloscope or signal-analyzer
   software, a CNC/3D-printer control UI, KiCad/PCB tooling, a SCADA/LabVIEW/PLC HMI, a
   microscope/lab-instrument console). Avoid talking-head/webcam-heavy tutorials — we want
   mostly screen content. Save to `corpus/real/` (gitignored). Make the URL a CLI arg with a
   sensible default; print clear errors. If downloads are blocked in this environment, FALL
   BACK to extending `corpus/make_synthetic.py`-style generation into a second, richer
   synthetic clip (`corpus/real/` or a new `corpus/synthetic/complex_demo.mp4` with its own
   labels) and clearly document that the "real" download was unavailable.
2. Run the full pipeline on the real clip:
   `.venv/bin/python -m procap.cli run corpus/real/<clip>.mp4`. Inspect the keyframes and
   segments. Do the extract thresholds (`change_threshold=0.0025`, `min_dwell_s=0.6`) produce
   sensible keyframes on real video (not thousands, not one)? Does golden/dross look plausible?
3. NEW `tests/test_integration.py` — an end-to-end test that runs all stages on a SMALL clip
   (use a few-second slice so CI is fast; you can `ffmpeg -t` trim the real clip or use the
   synthetic one if real is unavailable) and asserts the run produces non-empty
   keyframes.json, segments.json, and a procedure.md with ≥1 step — i.e. nothing crashes and
   the artifact contract holds. Mark it to be skipped if the clip is missing.
4. NEW `docs/decisions/2026-06-26-real-corpus-findings.md` (frontmatter `status: decided`):
   report what clip you used, the keyframe/segment counts, whether the default thresholds
   generalized, and your RECOMMENDED threshold changes if any (with the measured numbers that
   justify them). Do NOT edit `procap/extract.py` or `procap/golden.py` yourself — report the
   recommendation with evidence and the orchestrator will apply it. This keeps your branch to
   new files only.

## Hard constraints

- Touch ONLY: NEW `corpus/fetch_real.py`, NEW `tests/test_integration.py`, NEW
  `docs/decisions/2026-06-26-real-corpus-findings.md`, and you MAY append rows to `debt.md`.
  Do NOT edit `procap/*.py` or existing tests (another agent owns the VLM work) — this avoids
  worktree merge conflicts.
- Base all claims on MEASURED output (run it; read the JSON), never on assumption. If you
  report a threshold should change, show the changed-fraction numbers that prove it.

## Setup & validation

- Create a venv in your worktree: `python3 -m venv .venv && .venv/bin/pip install -e .[dev]`.
  `ffmpeg`/`ffprobe` are on PATH. `.venv/bin/python corpus/make_synthetic.py` makes the base clip.
- Keep the existing suite green and add your integration test: `.venv/bin/python -m pytest -q`.
- Commit on your branch when done (do not push). Leave tests passing.

End your response with a `## Takeaways` block: decisions made, gotchas hit (especially
anything about real-video keyframing behaving differently than synthetic), lessons worth keeping.
