#!/usr/bin/env python3
"""Jambubrowser CLI — AI-powered webapp auditing from your terminal.

This module is the entry point (`jambu = cli.jambu:main` in pyproject). It used
to be the whole implementation: shared transport, 20 command handlers and a
389-line `main()` that declared 49 subparsers *and* dispatched them. Those now
live in `cli/jambu_cli/`:

    core.py           config file, engine HTTP, exit codes, formatters
    commands/audit.py audit, quick, history, share, report, tiers, diff
    commands/status.py        auth, health, status
    commands/monitors.py      monitor
    commands/qa.py            qa, test, export, import, plan, record, watch
    commands/mesh.py          dcm, sim, vpn

The names below are re-exported so existing importers keep working. Note that
the *patch point* for tests and embedders is `cli.jambu_cli.core`: command
handlers call `core.api_request(...)`, so rebinding the copy in this module
would no longer intercept them. The re-exports are here for reading and for
`from cli.jambu import cmd_status`-style imports, not for monkeypatching.
"""
from __future__ import annotations

import sys

from cli.jambu_cli import build_parser, core, main as _main
from cli.jambu_cli.commands.audit import (
    cmd_audit,
    cmd_diff,
    cmd_history,
    cmd_quick,
    cmd_report,
    cmd_share,
    cmd_tiers,
)
from cli.jambu_cli.commands.mesh import cmd_dcm, cmd_sim, cmd_vpn
from cli.jambu_cli.commands.monitors import cmd_monitor
from cli.jambu_cli.commands.qa import (
    cmd_dev_servers,
    cmd_export,
    cmd_import,
    cmd_plan,
    cmd_qa,
    cmd_qa_dataset,
    cmd_record,
    cmd_test,
    cmd_watch,
)
from cli.jambu_cli.commands.status import cmd_auth, cmd_health, cmd_status

# Re-exported so `jambu.api_request` keeps resolving: it is the single patch
# point every CLI test uses to keep the engine off the network.
api_request = core.api_request
api_request_bytes = core.api_request_bytes
api_request_text = core.api_request_text
_dcm_request = core._dcm_request
load_config = core.load_config
save_config = core.save_config

EXIT_OK = core.EXIT_OK
EXIT_GATE_FAILED = core.EXIT_GATE_FAILED
EXIT_ENGINE_ERROR = core.EXIT_ENGINE_ERROR
SEVERITY_ORDER = core.SEVERITY_ORDER
SEVERITY_ICONS = core.SEVERITY_ICONS
SEVERITY_LABELS = core.SEVERITY_LABELS

__all__ = [
    "main",
    "api_request",
    "cmd_audit", "cmd_quick", "cmd_history", "cmd_share", "cmd_report",
    "cmd_tiers", "cmd_diff", "cmd_auth", "cmd_health", "cmd_status",
    "cmd_monitor", "cmd_dcm", "cmd_sim", "cmd_vpn", "cmd_qa",
    "cmd_qa_dataset", "cmd_test", "cmd_export", "cmd_import", "cmd_plan",
    "cmd_record", "cmd_watch", "cmd_dev_servers",
]


def main(argv=None) -> int:
    """Entry point. The implementation lives in cli.jambu_cli."""
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
