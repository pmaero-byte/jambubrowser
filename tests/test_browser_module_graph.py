"""The browser modules must have an acyclic import graph.

`browser_flow` and `browser_agent` were mutually dependent for a while: the
flow helpers imported back into `browser_agent` while `browser_agent` imported
from `browser_flow`. That works *only* when `browser_agent` happens to be
imported first — importing `browser_flow` on its own died with a
partial-initialisation ImportError, which is the kind of bug that shows up in a
test file nobody runs before CI does.

Python caches modules per process, so each check below runs in its own
interpreter: "import this module first, with nothing else preloaded".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every module in the browser-agent cluster. Order here is irrelevant — that is
# the point: each one must survive being the first import of the cluster.
BROWSER_MODULES = [
    "backend.modules.browser_agent_errors",
    "backend.modules.browser_debug",
    "backend.modules.browser_page",
    "backend.modules.browser_flow",
    "backend.modules.browser_assertions",
    "backend.modules.browser_step_actions",
    "backend.modules.browser_agent",
    "backend.modules.browser_agent_service",
]


@pytest.mark.parametrize("module", BROWSER_MODULES)
def test_module_imports_first_without_a_partial_import_error(module: str):
    """Importing this module first must not pull a half-initialised sibling."""
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"{module} cannot be imported first:\n{proc.stdout}\n{proc.stderr}"
    )


def test_browser_agent_still_exposes_its_public_names():
    """The split must not break the module other code imports from."""
    from backend.modules import browser_agent

    for name in (
        "BrowserAgentSession",
        "BrowserAgentService",
        "PlaywrightPage",
        "Telemetry",
        "PageAdapter",
        "summarise_coverage",
        "host_allowed",
        "classify_risk",
        "resolve_upload_paths",
        "render_flow_report",
        "normalize_flow_steps",
        "get_browser_agent_service",
        "reset_browser_agent_service",
        "SessionRefused",
    ):
        assert hasattr(browser_agent, name), f"browser_agent lost {name}"


def test_unknown_attribute_still_raises_attribute_error():
    from backend.modules import browser_agent

    with pytest.raises(AttributeError):
        browser_agent.no_such_name  # noqa: B018