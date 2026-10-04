"""Exceptions shared across the browser agent's submodules.

Lives in its own module so extracted subsystems (``browser_assertions`` and
friends) can raise the session's refusal type without importing the whole
1,500-line agent module — which is exactly the import cycle that made this
file awkward to split before.
"""

from __future__ import annotations

from typing import Optional


class SessionRefused(Exception):
    """A safety rail refused the action (allowlist, approval, SSRF).

    ``reason`` is a stable machine-readable slug (``invalid_url``,
    ``approval_required``, …) that routes and reports key on; ``detail`` is
    the human sentence. Keep ``detail`` specific: it is what an operator
    reads at 2am when a flow refuses.
    """

    def __init__(self, reason: str, detail: str = "", candidates: Optional[list] = None):
        self.reason = reason
        self.detail = detail
        self.candidates = candidates or []
        super().__init__(detail or reason)
