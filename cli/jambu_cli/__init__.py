"""The `jambu` CLI: parser construction and dispatch.

`cli/jambu.py` was one 2,108-line file holding the shared transport, 20 command
handlers and a 389-line `main()` that both declared 49 subparsers and dispatched
them, so adding a command meant editing two places in the same very large file.
The handlers now live in `commands/`, the transport in `core.py`, and this
module is the only place that knows the mapping from subcommand to handler.
"""
from __future__ import annotations

import argparse

from cli.jambu_cli import core
from cli.jambu_cli.commands import audit as audit_cmds
from cli.jambu_cli.commands import mesh as mesh_cmds
from cli.jambu_cli.commands import monitors as monitor_cmds
from cli.jambu_cli.commands import qa as qa_cmds
from cli.jambu_cli.commands import status as status_cmds

# Every command module contributes its subparsers here.
COMMAND_MODULES = (audit_cmds, status_cmds, monitor_cmds, qa_cmds, mesh_cmds)


def build_parser() -> argparse.ArgumentParser:
    """Build the full CLI parser. Each module registers its own subcommands."""
    try:
        from backend import __version__ as _v
    except Exception:
        # Installed without the backend package: the CLI can still print help.
        _v = "unknown"

    parser = argparse.ArgumentParser(
        prog="jambu",
        description="Jambubrowser CLI — AI-powered webapp auditing",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_v}")
    subparsers = parser.add_subparsers(dest="command")

    for module in COMMAND_MODULES:
        module.register(subparsers)
    return parser


# Subcommand -> handler. Handlers that return an int set the process exit code
# (CI relies on 0 = pass, 1 = gate failed, 2 = engine error); the rest return
# None and the exit code stays EXIT_OK.
DISPATCH = {
    "auth": status_cmds.cmd_auth,
    "audit": audit_cmds.cmd_audit,
    "quick": audit_cmds.cmd_quick,
    "history": audit_cmds.cmd_history,
    "share": audit_cmds.cmd_share,
    "report": audit_cmds.cmd_report,
    "tiers": audit_cmds.cmd_tiers,
    "diff": audit_cmds.cmd_diff,
    "health": status_cmds.cmd_health,
    "status": status_cmds.cmd_status,
    "monitor": monitor_cmds.cmd_monitor,
    "dcm": mesh_cmds.cmd_dcm,
    "sim": mesh_cmds.cmd_sim,
    "vpn": mesh_cmds.cmd_vpn,
    "test": qa_cmds.cmd_test,
    "export": qa_cmds.cmd_export,
    "import": qa_cmds.cmd_import,
    "plan": qa_cmds.cmd_plan,
    "record": qa_cmds.cmd_record,
    "watch": qa_cmds.cmd_watch,
    "dev-servers": qa_cmds.cmd_dev_servers,
    "qa": qa_cmds.cmd_qa,
}


def main(argv=None) -> int:
    """Parse argv, dispatch, and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return core.EXIT_OK

    handler = DISPATCH.get(args.command)
    if handler is None:
        parser.print_help()
        return core.EXIT_OK

    # `qa dataset` is a sub-subcommand of `qa`, not a sibling of it.
    if args.command == "qa" and getattr(args, "qa_command", None) == "dataset":
        handler = qa_cmds.cmd_qa_dataset

    result = handler(args)
    return core.EXIT_OK if result is None else result
