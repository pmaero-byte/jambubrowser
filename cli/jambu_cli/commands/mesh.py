"""DecentraCode Mesh, simulation compute and VPN egress.

Handlers call the shared transport and formatters as `core.<name>`, so a
test patches one object (`cli.jambu_cli.core`) no matter which command it
is exercising.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

from cli.jambu_cli import core


def cmd_dcm(args) -> int:
    """Operate a DecentraCode Mesh (DCM) node from the terminal."""
    sub = getattr(args, "dcm_command", None)
    if sub is None:
        print("Usage: jambu dcm {status,infer} ...")
        print("       jambu dcm status                    Node, mesh and model overview")
        print("       jambu dcm infer <prompt> [--model M] [--max-tokens N]")
        print("Set JAMBU_DCM_URL to point at a node (default http://127.0.0.1:3001).")
        return core.EXIT_OK

    if sub == "status":
        status, health = core._dcm_request("GET", "/health", timeout=5.0)
        if status == 0:
            return core.EXIT_ENGINE_ERROR
        print(f"\n🕸  DecentraCode node — {core.get_dcm_url()}")
        print(f"   Health: {'ok' if status == 200 else f'HTTP {status}'}")

        _, inf = core._dcm_request("GET", "/api/inference/status", timeout=10.0)
        if isinstance(inf, dict):
            top_ready = bool(inf.get("engine_ready", inf.get("ready")))
            moe = inf.get("moe") or {}
            moe_ready = bool(moe.get("ready") or moe.get("available"))
            # A top-level error only concerns the *default* runtime — a ready
            # secondary (MoE sidecar) means the node can still serve inference.
            if top_ready or moe_ready:
                if top_ready:
                    line = f"{inf.get('runtime', 'runtime')} ready"
                else:
                    line = f"{moe.get('runtime', 'MoE')} ready"
                    if inf.get("error"):
                        line += f" ({inf.get('runtime', 'default')}: {str(inf['error'])[:60]})"
            else:
                line = "not ready — " + str(inf.get("error") or "no runtime available")[:80]
            print(f"   Inference: {line}")
        else:
            print(f"   Inference: unavailable — {str(inf)[:100]}")

        _, models = core._dcm_request("GET", "/api/models", timeout=10.0)
        model_list = models.get("models", []) if isinstance(models, dict) else []
        if model_list:
            available = [
                m.get("id") for m in model_list
                if m.get("available") or m.get("status") in ("available", "ready")
            ]
            print(f"   Models: {len(available)} available"
                  + (f" — {', '.join(str(a) for a in available[:4])}" if available else ""))

        _, mesh = core._dcm_request("GET", "/api/network/status", timeout=10.0)
        if isinstance(mesh, dict) and not mesh.get("error"):
            peers = mesh.get("peers") or mesh.get("peer_count") or []
            n = len(peers) if isinstance(peers, (list, dict)) else peers
            print(f"   Mesh: {n} peer(s) · node {str(mesh.get('nodeId') or mesh.get('node_id') or '?')[:16]}")
        print()
        return core.EXIT_OK

    if sub == "infer":
        prompt = " ".join(args.prompt) if isinstance(args.prompt, list) else args.prompt
        prompt = (prompt or "").strip()
        if not prompt:
            print("Usage: jambu dcm infer <prompt> [--model M] [--max-tokens N]")
            return core.EXIT_OK
        status, resp = core._dcm_request(
            "POST", "/api/inference/v1/chat/completions",
            {
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": args.max_tokens,
                "stream": False,
                **({"model": args.model} if args.model else {}),
            },
            timeout=120.0,
        )
        if status == 0:
            return core.EXIT_ENGINE_ERROR
        if status != 200:
            detail = resp.get("error") if isinstance(resp, dict) else str(resp)[:200]
            code = resp.get("code") if isinstance(resp, dict) else ""
            print(f"\033[91m❌ DCM inference failed ({status}{f' {code}' if code else ''}): {detail}\033[0m")
            return core.EXIT_ENGINE_ERROR

        choice = (resp.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        usage = resp.get("usage") or {}
        print(f"\n🕸  DCM · {resp.get('model', '?')}\n")
        print(content.strip() or "(empty response)")
        print(f"\n   {usage.get('completion_tokens', 0)} completion tokens"
              + (f" · {usage.get('total_ms', 0) / 1000:.1f}s" if usage.get("total_ms") else ""))
        return core.EXIT_OK

    print(f"Unknown dcm subcommand: {sub}")
    return core.EXIT_OK


def cmd_sim(args) -> int:
    """Decentralised simulation compute: quote, dispatch+verify, inspect jobs."""
    sub = getattr(args, "sim_command", None)
    if sub is None:
        print("Usage: jambu sim {nodes,quote,run,jobs} ...")
        print("       jambu sim nodes                       Registered compute nodes")
        print("       jambu sim quote <module> [--steps N] [--replicas N]")
        print("       jambu sim run <module> [--steps N] [--replicas N] [--idempotency-key K]")
        print("       jambu sim jobs [--limit N] [--status S]")
        print("\nA job's spec is frozen and hashed before dispatch; replicas are")
        print("compared numerically and a disagreement is quarantined, not paid.")
        return core.EXIT_OK

    if sub == "nodes":
        resp = core.api_request("GET", "/simulation/nodes")
        if resp is None:
            return core.EXIT_ENGINE_ERROR
        nodes = resp.get("nodes") or []
        print(f"\n🧮  Simulation compute nodes — {core.get_engine_url()}")
        print(f"   {resp.get('available', 0)}/{resp.get('count', 0)} available\n")
        if not nodes:
            print("   (none registered)")
        for node in nodes:
            health = node.get("health") or {}
            rep = node.get("reputation") or {}
            flag = "✅" if health.get("available") else "⛔"
            score = rep.get("score")
            score_txt = "new" if score is None else f"{score:.2f}"
            line = (f"   {flag} {node.get('node_id')}  reputation={score_txt}  "
                    f"ok={health.get('successes', 0)} "
                    f"fail={health.get('failures', 0)}")
            if health.get("divergences"):
                line += f"  diverged={health['divergences']}"
            print(line)
            if health.get("quarantined"):
                print(f"        ⛔ quarantined after "
                      f"{health.get('consecutive_failures')} consecutive failures: "
                      f"{health.get('last_error', '')[:60]}")
            elif health.get("divergences"):
                print(f"        ⚠ diverged from mesh consensus "
                      f"{health['divergences']}x — deprioritised, not excluded")
        return core.EXIT_OK

    if sub in ("quote", "run"):
        payload = {
            "module": args.module, "kind": args.kind,
            "seed": args.seed, "replicates": args.replicas,
        }
        if args.steps:
            payload["steps"] = args.steps
        if sub == "run" and getattr(args, "idempotency_key", ""):
            payload["idempotency_key"] = args.idempotency_key
        if sub == "run" and getattr(args, "queued", False):
            payload["queued"] = True
        # The CLI verb is "run"; the route that dispatches work is /submit.
        endpoint = "quote" if sub == "quote" else "submit"
        resp = core.api_request("POST", f"/simulation/{endpoint}", payload)
        if resp is None:
            return core.EXIT_ENGINE_ERROR

        if sub == "quote":
            print(f"\n🧮  Simulation quote — {args.module}\n")
            print(f"   Spec hash:  {resp.get('spec_hash', '?')[:32]}…")
            print(f"   Work:       {resp.get('work_units')} units "
                  f"over {resp.get('replicates')} replica(s)")
            print(f"   Cost:       {resp.get('dct')} DCT → {resp.get('usdc')} USDC")
            print(f"   Tier:       {resp.get('tier')}")
            print(f"   Nodes:      {resp.get('nodes_available')} available")
            print(f"\n   {resp.get('rate_note', '')}")
            return core.EXIT_OK

        status = resp.get("status", "?")
        colour = {"SETTLED": "92", "QUARANTINED": "93", "FAILED": "91",
                  "QUEUED": "94", "RUNNING": "94"}.get(status, "0")
        print(f"\n🧮  Simulation — \033[{colour}m{status}\033[0m\n")
        print(f"   Job:       {resp.get('id')}")
        print(f"   Spec hash: {resp.get('spec_hash', '?')[:32]}…")
        print(f"   Nodes:     {', '.join(resp.get('nodes_used') or []) or '—'}")
        print(f"   Charged:   {resp.get('charged_dct')} DCT")
        verification = resp.get("verification") or {}
        if verification:
            print(f"   Verdict:   {verification.get('verdict')}"
                  + (f" ({verification.get('replicas_compared')} compared)"
                     if verification.get("replicas_compared") else ""))
            if verification.get("agreeing"):
                print(f"   Agreed:    {', '.join(verification['agreeing'])}")
            if verification.get("diverging"):
                print(f"   Diverged:  {', '.join(verification['diverging'])} "
                      f"— minority excluded from the result")
            if verification.get("disputed"):
                print(f"   Disputed:  {', '.join(verification['disputed'])} "
                      f"— no majority, nobody blamed")
            if verification.get("max_deviation_path"):
                print(f"   Worst dev: {verification.get('max_rel_deviation')} "
                      f"at {verification.get('max_deviation_path')}")
        for attempt in resp.get("attempts") or []:
            if not attempt.get("ok"):
                print(f"   ⚠ {attempt.get('node_id')} failed: {attempt.get('error')}")
        if resp.get("idempotent_replay"):
            print("   (idempotent replay — stored job returned, no new charge)")
        if resp.get("error"):
            print(f"   Reason: {resp['error']}")
        # A quarantined job is a real operational outcome, not a CLI crash:
        # the run completed and reported that nothing was charged.
        return core.EXIT_OK

    if sub == "jobs":
        query = f"/simulation/jobs?limit={args.limit}"
        if args.status:
            query += f"&status={args.status}"
        resp = core.api_request("GET", query)
        if resp is None:
            return core.EXIT_ENGINE_ERROR
        jobs = resp.get("jobs") or []
        totals = resp.get("totals") or {}
        print(f"\n🧮  Simulation jobs ({len(jobs)})\n")
        for job in jobs:
            print(f"   {job.get('status', '?'):<13} {str(job.get('spec_hash'))[:12]}…  "
                  f"{job.get('replicas')} replica(s)  {job.get('charged_dct')} DCT")
        if not jobs:
            print("   (no jobs yet)")
        print(f"\n   Total: {totals.get('jobs', 0)} jobs · "
              f"{totals.get('chargedDct', 0)} DCT charged · "
              f"{totals.get('unpaidJobs', 0)} unpaid")
        return core.EXIT_OK

    print(f"Unknown sim subcommand: {sub}")
    return core.EXIT_OK


def cmd_vpn(args):
    """Dynamic VPN control: status, tunnel up/down, pool health."""
    action = getattr(args, "vpn_command", None) or "status"

    # `up`/`down` need root + a vendor binary, so they drive the local
    # subsystem directly instead of going through the engine's HTTP API.
    if action in ("up", "down"):
        import asyncio

        from backend.core.vpn import get_vpn_manager, load_config

        config = load_config()
        if not config.tunnel_enabled:
            print("✗ No tunnel configured.")
            print("  Set JAMBU_VPN_TUNNEL (wireguard|openvpn) plus")
            print("  JAMBU_VPN_TUNNEL_INTERFACE and JAMBU_VPN_TUNNEL_ENDPOINT.")
            return core.EXIT_ENGINE_ERROR
        manager = get_vpn_manager(config)
        runner = manager.start if action == "up" else manager.stop
        # start()/stop() return the full status dict; the tunnel sub-object
        # carries the per-backend state we want to print.
        tunnel = asyncio.run(runner()).get("tunnel", {})
        if tunnel.get("state") == "up":
            print(f"✓ Tunnel {tunnel.get('kind')} is up "
                  f"({tunnel.get('interface') or 'no interface'})")
            return core.EXIT_OK
        print(f"✗ Tunnel {tunnel.get('kind')} {tunnel.get('state')}: "
              f"{tunnel.get('last_error') or 'unknown error'}")
        return core.EXIT_ENGINE_ERROR

    if action == "leak-check":
        resp = core.api_request("GET", "/vpn/leak-check")
        if not resp:
            return core.EXIT_ENGINE_ERROR
        core._section("Leak check")
        print(f"  verdict    {resp.get('verdict')}")
        print(f"  direct IP  {resp.get('direct_ip') or '—'}")
        print(f"  tunnel IP  {resp.get('tunnel_ip') or '—'}")
        ipv6 = resp.get("ipv6") or {}
        print(f"  IPv6       direct={ipv6.get('direct') or '—'} tunnel={ipv6.get('tunnel') or '—'}")
        dns = resp.get("dns") or {}
        print(f"  DNS        verdict={dns.get('verdict')} "
              f"system={dns.get('system_answer') or '—'} tunnel={dns.get('tunnel_answer') or '—'}")
        for leak in resp.get("leaks") or []:
            print(f"  ✗ {leak}")
        for note in resp.get("notes") or []:
            print(f"  · {note}")
        return core.EXIT_OK if resp.get("verdict") in ("ok", "disabled") else core.EXIT_GATE_FAILED

    resp = core.api_request("GET", "/vpn/status")
    if not resp:
        return core.EXIT_ENGINE_ERROR

    if not resp.get("enabled"):
        print("\n  Dynamic VPN is disabled.")
        print("  Set JAMBU_VPN_ENABLED=1 and configure a pool or tunnel.")
        return core.EXIT_OK

    tunnel = resp.get("tunnel", {})
    core._section("Tunnel")
    print(f"  kind      {tunnel.get('kind', 'none')}")
    print(f"  state     {tunnel.get('state', 'down')}")
    if tunnel.get("interface"):
        print(f"  interface {tunnel['interface']}")
    if tunnel.get("last_error"):
        print(f"  error     {tunnel['last_error']}")

    pool = resp.get("pool", {})
    core._section(f"Pool ({pool.get('healthy', 0)}/{pool.get('size', 0)} healthy)")
    print(f"  rotation  {pool.get('rotation', '?')}")
    for ep in pool.get("endpoints", []):
        icon = "✓" if ep["available"] else "✗"
        latency = f"{ep['latency_ms']}ms" if ep.get("latency_ms") else "—"
        print(f"  {icon} {ep['url']}  {latency}  "
              f"({ep['successes']}ok/{ep['failures']}fail)")

    problems = resp.get("problems") or []
    if problems:
        core._section("Configuration problems")
        for problem in problems:
            print(f"  ✗ {problem}")
        return core.EXIT_ENGINE_ERROR
    return core.EXIT_OK


def register(subparsers) -> None:
    """Declare the mesh commands: dcm, simulation compute and vpn."""
    p_vpn = subparsers.add_parser(
        "vpn", help="Dynamic VPN status and tunnel control"
    )
    vpn_sub = p_vpn.add_subparsers(dest="vpn_command")
    vpn_sub.add_parser("status", help="Show tunnel + pool health")
    vpn_sub.add_parser("up", help="Bring the VPN tunnel up")
    vpn_sub.add_parser("down", help="Take the VPN tunnel down")
    vpn_sub.add_parser("leak-check", help="Probe whether the egress actually avoids leaks")

    p_dcm = subparsers.add_parser(
        "dcm", help="Operate a DecentraCode Mesh node (status, infer)",
    )
    d_sub = p_dcm.add_subparsers(dest="dcm_command")
    d_sub.add_parser("status", help="Node, mesh, and model overview")

    p_d_infer = d_sub.add_parser("infer", help="Run a prompt on the mesh")
    p_d_infer.add_argument("prompt", nargs="+", help="Prompt text")
    p_d_infer.add_argument("--model", default=None,
                           help="DCM model id (default: node default)")
    p_d_infer.add_argument("--max-tokens", dest="max_tokens", type=int,
                           default=64)

    p_sim = subparsers.add_parser(
        "sim", help="Decentralised simulation compute (quote, run, jobs)",
    )
    sim_sub = p_sim.add_subparsers(dest="sim_command")
    sim_sub.add_parser("nodes", help="Registered simulation compute nodes")

    p_sim_q = sim_sub.add_parser("quote", help="Price a job (no dispatch)")
    p_sim_q.add_argument("module", help="Simulation module (heat, fluid, solve)")
    p_sim_q.add_argument("--kind", default="native",
                         choices=["wasm", "inference", "native"])
    p_sim_q.add_argument("--steps", type=int, default=0,
                         help="Simulation steps (0 = engine default)")
    p_sim_q.add_argument("--seed", type=int, default=0)
    p_sim_q.add_argument("--replicas", type=int, default=1)

    p_sim_r = sim_sub.add_parser("run", help="Dispatch, verify, and settle a job")
    p_sim_r.add_argument("module", help="Simulation module (heat, fluid, solve)")
    p_sim_r.add_argument("--kind", default="native",
                         choices=["wasm", "inference", "native"])
    p_sim_r.add_argument("--steps", type=int, default=0)
    p_sim_r.add_argument("--seed", type=int, default=0)
    p_sim_r.add_argument("--replicas", type=int, default=1)
    p_sim_r.add_argument("--idempotency-key", default="",
                         help="Retries return the same job (never double-charges)")
    p_sim_r.add_argument("--queued", action="store_true",
                         help="Enqueue and let the durable worker settle it")

    p_sim_j = sim_sub.add_parser("jobs", help="Job history and spend")
    p_sim_j.add_argument("--limit", type=int, default=20)
    p_sim_j.add_argument("--status", default="",
                         choices=["", "SETTLED", "FAILED", "QUARANTINED"])
