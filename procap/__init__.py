"""procap — screenshare video -> time-estimated written procedure.

Pipeline: extract (video -> keyframes) -> golden (keyframes -> golden/dross segments)
-> procedure/audit (golden segments -> procedure, compared against a written doc).
Stages hand off via JSON artifacts in a run dir (see procap.run). Heuristics are the
always-on baseline; procap.vlm enriches when an ANTHROPIC_API_KEY is present.
"""

__version__ = "0.1.0"
