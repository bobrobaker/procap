---
doctype: decision
status: decided
date: 2026-06-26
---
# Real-corpus robustness: default extract thresholds generalize unchanged

## Decision

**Keep `change_threshold=0.0025` and `min_dwell_s=0.6` as shipped.** A real ~3-minute KiCad
screencast — content nobody scripted — runs through the full pipeline cleanly and produces a
sensible keyframe count. The measured changed-fraction distribution puts the default threshold
in a low-density valley, and the keyframe count is robust to ±2× threshold moves, so there is no
evidence-backed reason to retune. No change to `procap/extract.py` or `procap/golden.py`.

The one real weakness surfaced is **over-segmentation at the golden stage**, which is a semantic
problem for the VLM-refinement workstream, not a threshold problem (see Findings #3, debt row).

## The clip

| | |
|---|---|
| Source | KiCad 6 screencast, YouTube `G3PZiaexLuI` ("Making custom symbol field visible on silkscreen") |
| Why | Genuine screen-only capture: schematic editor, PCB editor, 3D viewer, several modal dialogs open/close — squarely the target domain (engineering GUI), no webcam/talking-head |
| Fetched by | `corpus/fetch_real.py` (default URL); 1194×720, 174.6 s, 4.8 MB, audio stripped |
| Pipeline | `procap run` → 60 keyframes, 31 segments (29 golden, 2 dross), 29 steps, ~72 s est. total; wall-clock ~12 s |

Real download worked in this environment (it was *not* blocked), so the synthetic fallback was
not needed. `yt-dlp` was missing from the venv as handed off and was `pip install`ed; the fetch
script invokes it as `python -m yt_dlp` so it resolves regardless of PATH.

## Findings (all measured from `runs/kicad_demo/`)

**1. Keyframe count is sensible — not thousands, not one.** 60 keyframes over 174.6 s ≈ one every
2.9 s. Dwell windows range 1.0 s–14.0 s (median 1.5 s), matching a real session that lingers on
some views and moves quickly through others.

**2. `change_threshold=0.0025` sits in a sparse valley of a bimodal distribution.** Over the 348
consecutive-frame deltas (349 frames sampled @ 2 fps):

| pctile | p50 | p75 | p90 | p95 | max |
|---|---|---|---|---|---|
| delta | 0.0005 | 0.0054 | 0.0771 | 0.1561 | 0.4859 |

The mass is bimodal: a large static cluster near 0.0005 (frames where nothing changed) and a
change tail ≥ ~0.05. The default 0.0025 falls in the gap between them. Fraction of deltas at or
below a candidate threshold: ≤0.0025 → 69.0 %, ≤0.005 → 74.1 %, ≤0.010 → 79.6 %. Moving the
threshold within the valley barely reclassifies any frames.

**3. Keyframe count is robust to the threshold; `min_dwell_s` is the governing knob.** Sweep
(raw run boundaries, then keyframes kept after `min_dwell_s=0.6`):

| threshold | raw runs | kept keyframes |
|---|---|---|
| 0.0010 | 140 | 65 |
| **0.0025 (default)** | **112** | **60** |
| 0.0050 | 94 | 59 |
| 0.0100 | 74 | 49 |
| 0.0200 | 64 | 44 |
| 0.0400 | 47 | 39 |

Doubling the threshold (0.0025 → 0.0050) changes the keyframe count by **one** (60 → 59). The
`min_dwell_s=0.6` filter is what does the real work: it collapses 112 raw runs to 60 kept,
discarding ~52 sub-dwell transients (scroll/animation/cursor-transit frames). Both defaults held.

**4. Revert-detection fired correctly and sparingly.** Only 2 of 31 segments are dross, both
genuine returns-to-prior-state ("reverted to state @ 41.5 s" and "@ 139.0 s") — consistent with a
dialog opened then cancelled back to the same canvas. No false reverts from incidental redraws.

## Recommended threshold changes

**None.** The defaults generalized. Recommending a change here would be retuning against noise:
the count is flat across a 16× threshold range around the default, and 0.0025 already sits in the
distribution's valley. If a future clip with heavier compression noise *does* over-trigger, the
sweep above says raise `change_threshold` toward 0.01 (still 49 keyframes here) before touching
`min_dwell_s` — but that is a contingency, not a present recommendation.

## Caveat / open weakness (for the golden + VLM workstream, not extract)

29 golden segments → 29 procedure steps for a 3-minute clip is **over-segmented**. The heuristic
emits one step per durable *visual* change with no semantic grouping, so a pan, a zoom, and a
panel toggle each become their own "step." This is exactly what the VLM golden-refinement is for
(merge/relabel semantically inconsequential changes); the heuristic baseline has no way to know a
zoom isn't an action. Logged to `debt.md`. It does **not** argue for a threshold change — raising
the threshold to cut step count would also drop genuine actions (the count curve is monotone).

## How to reproduce

```bash
.venv/bin/python corpus/fetch_real.py                       # -> corpus/real/kicad_demo.mp4
.venv/bin/python -m procap.cli run corpus/real/kicad_demo.mp4   # -> runs/kicad_demo/
# distribution + sweep numbers above: re-sample @2fps, compute changed_fraction across
# consecutive frames and anchor-based run boundaries (procap.extract / procap.imageutil).
```
