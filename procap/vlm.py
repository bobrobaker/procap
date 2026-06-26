"""The vision-LLM seam. One class, one low-level method (`ask`), one availability flag.

Design rule (see docs/decisions/2026-06-26-pipeline-and-contracts.md): the VLM is an
*enhancement*, never a hard dependency. Stages must check `VLM.available` and take a
heuristic path when it is False. Tests mock `VLM.ask` to exercise the keyed path without
network or credits.

With no ANTHROPIC_API_KEY (or no `anthropic` package), `available` is False and `ask`
raises — so a stage that forgets to branch fails loud rather than silently degrading.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

# Latest capable vision model as of this writing; override via VLM(model=...).
DEFAULT_MODEL = "claude-opus-4-8"


class VLMUnavailable(RuntimeError):
    """Raised when ask() is called without a usable backend."""


class VLM:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    @property
    def available(self) -> bool:
        if not self._api_key:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_client(self):
        if self._client is None:
            if not self.available:
                raise VLMUnavailable(
                    "no ANTHROPIC_API_KEY (or anthropic not installed) — "
                    "call only when .available is True"
                )
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    @staticmethod
    def _encode_image(path: str | Path) -> dict:
        p = Path(path)
        media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": data},
        }

    def ask(self, prompt: str, image_paths: list[str] | None = None,
            max_tokens: int = 1024) -> str:
        """Single multimodal turn: text prompt + optional images -> text reply.

        Raises VLMUnavailable when no backend; callers must guard on `available`.
        """
        client = self._ensure_client()
        content: list[dict] = []
        for ip in (image_paths or []):
            content.append(self._encode_image(ip))
        content.append({"type": "text", "text": prompt})
        resp = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")


_default: VLM | None = None


def default() -> VLM:
    """Process-wide VLM singleton; cheap to call from any stage."""
    global _default
    if _default is None:
        _default = VLM()
    return _default
