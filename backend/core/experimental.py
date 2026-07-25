"""
Experimental feature gate
=========================
Some surfaces in the codebase are experimental or depend on external
infrastructure that is not shipped with Jambubrowser (see
``docs/FEATURE_MAP.md``). They are gated behind the ``JAMBU_ENABLE_EXPERIMENTAL``
environment variable, which defaults to **disabled**.

Set ``JAMBU_ENABLE_EXPERIMENTAL=1`` (or ``true``/``yes``/``on``) to opt in.
"""

import os

from fastapi import HTTPException

ENV_FLAG = "JAMBU_ENABLE_EXPERIMENTAL"

_TRUTHY = ("1", "true", "yes", "on")


def experimental_enabled() -> bool:
    """Return True when experimental features are explicitly opted into."""
    return os.environ.get(ENV_FLAG, "").strip().lower() in _TRUTHY


def require_experimental(feature: str) -> None:
    """Raise HTTP 501 when the experimental gate is disabled."""
    if not experimental_enabled():
        raise HTTPException(
            status_code=501,
            detail=(
                f"{feature} is experimental and disabled; "
                f"set {ENV_FLAG}=1 to enable."
            ),
        )
