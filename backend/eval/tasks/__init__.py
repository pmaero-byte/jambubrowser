"""Eval task suites — GAIA-style, WebArena-style, ALFWorld, WebShop, SWE-bench, and Jambubrowser-specific."""

# Importing these modules registers their tasks via register_task().
from . import gaia_mini      # noqa: F401
from . import webarena_mini   # noqa: F401
from . import privacy         # noqa: F401
from . import memory          # noqa: F401
from . import smoke           # noqa: F401

# Phase 5 benchmark suites (HarnessX evaluation)
from . import gaia            # noqa: F401
from . import alfworld        # noqa: F401
from . import webshop         # noqa: F401
from . import swebench        # noqa: F401
