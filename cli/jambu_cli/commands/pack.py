"""`jambu pack list|run`: run a test pack in-process, no engine API needed.

    jambu pack run oviyan --base http://127.0.0.1:7878 --json out.json

Exit codes follow the rest of the CLI: 0 every step passed, 1 a step failed
(including known findings that still reproduce), 2 the pack could not run
(bad pack, missing variable, target unreachable, a flow crashed).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cli.jambu_cli import core


def _parse_vars(pairs) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"--var expects name=value, got {pair!r}")
        name, value = pair.split("=", 1)
        out[name.strip()] = value
    return out


def cmd_pack(args) -> int:
    from backend.modules import test_packs as tp

    if getattr(args, "pack_command", None) == "list":
        for pack_dir in tp.pack_dirs():
            try:
                pack = tp.load_pack(pack_dir)
            except tp.PackError as exc:
                print(f"  {pack_dir.name:<16} (invalid: {exc})")
                continue
            print(f"  {pack_dir.name:<16} {len(pack['flows'])} flows — "
                  f"{pack.get('description', '')[:90]}")
        return core.EXIT_OK
    if getattr(args, "pack_command", None) != "run":
        print("Usage: jambu pack {list,run} ...")
        return core.EXIT_OK
    return run_pack_command(args)


def run_pack_command(args, service=None) -> int:
    import asyncio

    from backend.modules import test_packs as tp

    try:
        pack_dir = tp.find_pack(args.pack)
        pack = tp.load_pack(pack_dir)
        overrides = _parse_vars(args.var)
        variables = tp.resolve_vars(pack, pack_dir, args.base, overrides)
        flows = [f.strip() for f in (args.flow or "").split(",") if f.strip()]
        result = asyncio.run(tp.run_pack(
            pack, pack_dir, variables, service=service, flow_ids=flows or None,
            update_baselines=args.update_baselines, artifacts_dir=args.artifacts,
        ))
    except (tp.PackError, ValueError) as exc:
        print(f"✗ pack: {exc}", file=sys.stderr)
        if args.json:
            Path(args.json).write_text(json.dumps(
                {"pack": args.pack, "ok": False, "error": str(exc)}, indent=2))
        return core.EXIT_ENGINE_ERROR
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(tp.format_text(result))
    if args.json:
        print(f"report: {args.json}")
    return tp.exit_code(result)


def register(subparsers) -> None:
    p = subparsers.add_parser("pack", help="Run a bundled test pack (e.g. oviyan)")
    sub = p.add_subparsers(dest="pack_command")
    sub.add_parser("list", help="List bundled packs")
    run = sub.add_parser("run", help="Run a pack against a base URL")
    run.add_argument("pack", help="Pack name under packs/ or a path to a pack dir")
    run.add_argument("--base", required=True, help="Base URL of the app under test")
    run.add_argument("--json", help="Write the full JSON report here")
    run.add_argument("--var", action="append", metavar="NAME=VALUE",
                     help="Set a pack variable (repeatable)")
    run.add_argument("--flow", help="Comma-separated flow ids to run (default: all)")
    run.add_argument("--update-baselines", action="store_true",
                     help="Rewrite visual baselines instead of comparing")
    run.add_argument("--artifacts", help="Artifacts directory (default ./jambu-pack-artifacts)")
