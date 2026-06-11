"""Eval task suites — GAIA-style, WebArena-style, and Jambubrowser-specific."""

# Importing these modules registers their tasks via the @register_task decorator.
from . import gaia_mini  # noqa: F401
from . import webarena_mini  # noqa: F401
from . import privacy  # noqa: F401
from . import memory  # noqa: F401
from . import smoke  # noqa: F401
