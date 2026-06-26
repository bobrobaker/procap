---
name: confer
description: Run a bounded, turn-based negotiation between two sessions (different repos/goals) over a shared thread file — converge on a cross-cutting decision, escalate to the user decision-ready if stuck, archive the thread as provenance. Use when the user invokes /confer <other-repo> [topic] [--listen] [--wait <minutes>], asks this session to "align / talk it out with the other session", or when a confer thread is awaiting this repo. NOT for one-way context transfer to a future session (that's /handoff).
---

# confer

You are one side of a **two-session negotiation** — two Claude sessions in different
repos converging on a cross-cutting decision themselves, over a shared thread file,
instead of using the user as a courier. The thread either **resolves**, **escalates to
the user** decision-ready, or **parks** (goes async). It never becomes open-ended
correspondence.

**Anti-goal:** a convergence device, not a chat log. No pleasantries, no restating the
other side back at them. Every turn either moves the decision or ends the thread.

## Gotchas

- **Both sessions started at once → give exactly one side `--listen`.** Two initiators
  make two threads for one decision. Before initiating, scan for an open thread on the
  same decision (match on *topic*, not filename); if a race still happens, the
  earlier-created file wins — the other initiator deletes its thread and replies on the
  survivor.
- **A baton naming you with no complete turn behind it is a torn write, not your cue.**
  If `awaiting:` is you but the matching `## Turn N` body is missing or truncated, you
  woke mid-write — wait ~2s and re-read **bypassing cached file state** (a shell
  `grep`/`sed`, or re-read after a beat) before acting. Re-reading through a stale view
  just re-confirms a turn that is already there.
- **A cited doc that looks missing or contradictory mid-confer may be mid-write, not
  wrong.** The other live session edits its own repo's docs (decisions, charters) *as it
  argues* — so a cited authority that's absent, empty, or seemingly contradicts ratified
  docs may simply not be written yet. Re-read after a beat before rebutting "your source
  doesn't exist / contradicts the record"; treat a freshly-absent cited authority like a
  torn write, not proof the other side is wrong.
- **On a watch timeout, scan for *any* confer thread awaiting you before going idle** —
  across *both* candidate locations (landing zone + repo-root `handoffs/`, per the
  Protocol-0 scan), never just one. A cross-fire — both sides initiating different but
  entangled threads — leaves a baton
  waiting on a file you were never watching.

## The thread file

Lives in the **landing zone's `handoffs/`** while open (`$CMS_LANDING_ZONE/handoffs/`,
else the repo's own `handoffs/` when no landing zone is set — note the repo-local
fallback only works if both sessions can see that path). Filename:
`YYYY-MM-DD-confer-<topic-slug>.md`.

```yaml
---
type: confer
topic: <one line — the decision to be made>
between: [<repo-a>, <repo-b>]
created: YYYY-MM-DD
awaiting: <repo whose turn it is>   # a repo name, or `user` (held for a steer), or `none` (resolved)
turn: 1
max_turns: 6        # total messages, both sides; default 6
status: open        # open | resolved | needs-user
---
```

**Identity:** your repo name is the basename of your repo root (`git rev-parse
--show-toplevel`) — the name used in `between:` and `awaiting:`. A file is a confer thread
for you **only if** its frontmatter is `type: confer` and `between:` includes your repo. A
handoff file is never a confer thread, whatever its name suggests.

**A good turn** states four things: **position**, the **load-bearing reasons** (drop the
rest), **what would change your mind**, and **concrete questions** for the other side. The
opener also states what the thread must decide and any hard constraints from its repo. The
four-part opener does real convergence work — keep all four in Turn 1.

## Protocol

0. **Role assignment.** Without `--listen` you are the **initiator** — but first check for
   an open `type: confer` thread on the same decision (match on topic) and reply there if
   one exists. With `--listen` you are the **listener** — never initiate; watch until a
   thread appears awaiting your repo. Scan **both** candidate locations — the landing zone
   (if set) *and* the repo-root `handoffs/`, the same two-location resolution `handoff` /
   `housekeep` use. Never a `:-.` cwd fallback: under an asymmetric `CMS_LANDING_ZONE` (set
   for one session, unset for the other) it silently watches the wrong dir and misses the
   thread.
   ```bash
   n=0
   scan() { for base in "${CMS_LANDING_ZONE:+$CMS_LANDING_ZONE/handoffs}" "$(git rev-parse --show-toplevel 2>/dev/null)/handoffs"; do
     [ -d "$base" ] || continue
     grep -l '^type: confer' "$base"/*.md 2>/dev/null | while read -r f; do
       grep -l 'awaiting: <your-repo>' "$f" 2>/dev/null   # while-read, not xargs: empty input must run nothing (xargs would read stdin)
     done
   done; }
   until [ -n "$(scan)" ]; do [ $n -ge 27 ] && break; sleep 10; n=$((n+1)); done
   ```
   (The deepest fix for the asymmetry is environmental — set `CMS_LANDING_ZONE` in global
   `~/.claude/settings.json` so every session inherits it; this two-location scan is the
   defense-in-depth.)
   (run in background). On exit, do one final fresh scan before declaring nothing arrived
   — the timeout and the file's arrival can race — then fall back to async pick-up.
0.5. **Pin the thread.** The moment you initiate or take a first turn, that file is *the*
   thread for the session — record its path, watch and write only it, never rescan
   mid-exchange.
   - **Surface it in the watch-grid (optional).** If `$SPAWN_STAGE` is set, make the
     negotiation watchable alongside spawned/relay agents by tiling a live tail of the
     pinned thread into the grid (magenta, `CONFER` badge):
     `agent-spawn tile --into "$SPAWN_STAGE" --name "confer-$(basename "$pinned" .md)" --color magenta --origin CONFER -- tail -f "$pinned"`.
     Remove it at close-out with `agent-spawn kill "confer-…"`. Inert when no grid is
     configured — don't block the exchange on it.
1. **Baton rule — write only when `awaiting:` names your repo; never edit prior turns.**
   - **Commit a turn as one write:** read fresh, compose the full updated file (prior
     turns untouched + your new `## Turn N` + frontmatter with `turn:` incremented and
     `awaiting:` flipped), write once. If you must edit in place, **flip the baton last** —
     the flip is the commit.
   - **Read fresh before every write** — base it on a read taken *now*, not a pre-sleep
     snapshot; if the file changed, re-read and rebase.
   - **A turn ending in a user question holds the baton.** Set `awaiting: user` (status
     stays `open`), ask the user in your session, fold in their answer, *then* flip to the
     other repo. (Lighter than a §3 escalation — you resume the negotiation yourself.)
2. **Converge-first.** Before drafting a reply, ask: do we now agree, or is the gap
   preference not substance? If agree — don't take another turn. Write `## Resolution` (the
   decision in one paragraph + who implements what), `status: resolved`, `awaiting: none`.
3. **Escalate when stuck.** If `turn == max_turns` without resolution, or the gap hinges on
   a judgment only the user can make (priorities, taste, money, scope), stop and write
   `## For the user` — both options, each side's recommendation and strongest argument,
   pre-packaged to decide in seconds. Set `status: needs-user` and tell the user in your
   session.
4. **Live mode (default when both sessions run).** After writing, background-watch the
   *pinned* file and take your next turn when the baton returns:
   ```bash
   f="<pinned-thread-path>"; m=$(stat -c %Y "$f"); n=0
   while [ "$(stat -c %Y "$f")" -eq "$m" ] && [ $n -lt 18 ]; do sleep 15; n=$((n+1)); done
   ```
   Patience defaults to **4.5 min** (just inside the 5-min prompt-cache TTL, so the wake-up
   reloads context at cached rates); `--wait <minutes>` extends it. On wake, **re-read** and
   branch on `awaiting:`: **you** → confirm a complete `## Turn N` body, then take your turn
   (torn write → Gotchas); **`user`** → the other side holds for a steer, keep holding,
   re-arm; **other repo** → an intermediate write not yet committed to you, re-arm;
   **timed out** → the thread **parks** (do the cross-fire scan from Gotchas, tell the user,
   move on). Treat `resolved`/`needs-user` and a move-to-archive as wake conditions too, not
   just the baton flip.
5. **Async mode.** A parked or cold thread is picked up like a handoff: at session start (or
   when the user points at it), scan **both** candidate locations (per the Protocol-0 scan)
   for `type: confer`, `status: open`, `awaiting: <this repo>`. Reply, and re-enter live mode
   only if the user says the other session is running.

## Lifecycle

- **Close-out:** when a thread resolves, **land the outcome first** — the decision goes into
  the owning repo's spec/roadmap/decision shelf and any durable lesson is codified — *then*
  move the thread to `handoffs/archive/` (add `archived: YYYY-MM-DD`). It is provenance: kept
  for the audit trail, never cited as evidence. **The side that writes the `## Resolution`
  archives it in the same close-out — don't punt the archive to the other side.** A
  resolved-but-unarchived thread reads as "still open / still awaiting a reply" and leaves
  "who archives?" ambiguous. Close-out is a one-side action — if the file is already gone,
  the other side closed it; verify the resolution landed, don't re-stamp.
- **Staleness:** a thread open ≳1 week means the decision stopped mattering or was made
  out-of-band — confirm with the user, record the actual outcome, archive.
- **One thread per decision.** A new disagreement on a different seam gets a new thread.

## Recent changes
<!-- lean anti-loop slice; full dated provenance in feedback.md -->

- A turn ending in a user question now *holds the baton* (`awaiting: user`), replacing a
  fuzzy inference that let the other side barge ahead.
- Watch loops wake on `resolved`/`needs-user`/archive-move, not just the baton flip — a
  listener once slept through the other side converging and the watched path vanished.
- A baton without a complete turn body is re-read (cache-bypassed), not answered.
- A cited doc that's missing/contradictory mid-confer may be mid-write — re-read before
  rebutting; the other live session edits its own repo's docs as it argues.

Full dated log: [feedback.md](feedback.md).
