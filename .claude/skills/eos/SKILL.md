---
name: eos
description: End-of-session closeout — run the three wrap-up steps in order: mine the session for takeaways, archive the session, then commit the work. Use when the user invokes /eos, says "close out" / "end of session" / "wrap up for the day", or otherwise signals the session is done.
---

# eos — end-of-session closeout

Run these three in order, pausing between each — don't batch them, since each can
change what the next one sees. If a step surfaces something needing the user's call,
stop there and ask.

1. **Mine** — invoke the `mine-session` skill. Capture reusable takeaways while the
   session context is still fresh (this can write to the takeaway store).
2. **Wrap** — invoke the `wrap-session` skill to archive a findable session summary.
3. **Commit** — stage and commit this session's work (`git add -A && git commit`,
   then push if the repo's workflow expects it). Propose a message; include anything
   steps 1–2 wrote, so the session archive lands in the commit.
