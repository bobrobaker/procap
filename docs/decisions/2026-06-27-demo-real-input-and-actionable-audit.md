---
doctype: decision
status: decided
date: 2026-06-27
---
# Demo accepts real input; the audit is made actionable; audited runs route to conformance

## Decision

Three coupled changes to the web demo (`procap/webdemo.py`):

1. **Real input.** The demo gained a `POST /run` upload path: a user uploads a screen
   recording (+ an optional SOP) and ProCap runs the full pipeline on it server-side, then
   redirects to the result. This reverses the prior "in-memory only, no server write path"
   choice (see `handoffs/2026-06-26 ProCap demo-restructure-and-confer.md`). The motive: the
   canned demo's Save-as-PDF only exported a copy of a fixed result — not useful. With real
   input the PDF becomes a genuine artifact of the user's own recording.

2. **Actionable audit.** Each conformance finding now renders side-by-side — the SOP wording
   beside the actual **screencap** of what the recording did at that step — and the **reference
   SOP itself** is rendered on the page (it was never loaded before; `written_doc` was only a
   path). An author can now see a divergence, not just be told a step number.

3. **Audited runs route to conformance.** `RunView.mode` previously routed a *count-only*
   audit to note-taking ("nothing to match yet"). That hid an uploaded user's SOP entirely
   when no VLM key was set (offline → placeholder titles → count audit). Now **any** run that
   carries an audit is conformance; the audit *method* (vlm/lexical/count) only bounds what the
   review can find, stated honestly in-copy.

Also: dismissed conformance findings now drop from the printed PDF (mirroring how retagged
dross drops in note-taking); a one-line honesty caveat explains the out-of-order check is a
greedy monotonic comparison, not full sequence alignment.

## Why

- **Save-as-PDF was hollow without real input.** Editing a fixed demo result and "saving" it
  is a copy, not an artifact. The upload path is what gives the export a reason to exist.
- **The audit couldn't drive an SOP fix.** A bare "step 4 out of order" line is unactionable;
  the screencap + reference SOP make the divergence inspectable, which is the whole point of an
  audit aimed at improving the written doc.
- **Count-audit-as-notetaking silently dropped user intent.** A user who provides an SOP is
  asking "check my recording against this." Routing offline that to note-taking discarded the
  SOP. Showing it with an honest "needs a VLM for content matching" note keeps the intent
  visible instead of swallowing it.

## Engineering notes

- The end-to-end run is factored into `cli.run_pipeline()` (`PipelineResult`), the single
  source of truth shared by `procap run` and the web upload path — not a parallel copy.
- Multipart parsing is a minimal stdlib parser (`_parse_multipart`), **not** `cgi` (deprecated;
  removed in Python 3.13). It strips exactly one leading/trailing CRLF so binary video payloads
  ending in CR/LF survive intact.
- Bounded for a local demo: 50 MB upload cap (client + server), allowed video extensions, and
  retention of the last 5 `upload_*` runs (`_prune_uploads`). The POST is synchronous with a
  JS processing overlay — no async job queue.
- The order check remains a greedy best-match + monotonicity test (blames the later-in-video
  step on a swap), not LCS/edit-distance alignment. Disclosed in-copy rather than "fixed",
  since principled alignment is out of scope for this pass.
