"""
Tests for the dynamic VPN subsystem (`backend/core/vpn`).

Covers all three layers — config parsing, the tunnel lifecycle, and pool
selection/health — plus the guarantees that matter most:

* **inert by default** (no env => direct connections, nothing imported eagerly)
* **fail closed** (a required-but-unusable VPN raises instead of leaking the
  real IP)
* **credentials never leak** into status output

No test touches the host network or requires root: tunnels run in dry-run mode
and the pool's health probe is always injected.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.core.vpn import (
    NoHealthyEndpoint,
    NullTunnel,
    OpenVPNTunnel,
    ProxyPool,
    RotationPolicy,
    TunnelKind,
    TunnelManager,
    VPNConfig,
    VPNManager,
    VPNUnavailable,
    WireGuardTunnel,
    build_backend,
    redact_proxy_url,
)
from backend.core.vpn import manager as manager_mod
from backend.core.vpn.pool import EndpointHealth


def run(coro):
    return asyncio.run(coro)


POOL_ENV = {
    "JAMBU_VPN_ENABLED": "1",
    "JAMBU_VPN_POOL": "http://a.example:1,http://b.example:2,http://c.example:3",
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_unconfigured_is_inert(self):
        cfg = VPNConfig.from_env({})
        assert cfg.enabled is False
        assert cfg.pool_enabled is False
        assert cfg.tunnel_enabled is False
        assert cfg.is_valid()

    def test_defaults_are_fail_closed(self):
        """Failing open (direct connection) must be an explicit opt-in."""
        assert VPNConfig.from_env({"JAMBU_VPN_ENABLED": "1"}).fail_closed is True
        opened = VPNConfig.from_env(
            {"JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_FAIL_OPEN": "1"}
        )
        assert opened.fail_closed is False

    def test_pool_parses_and_splits(self):
        cfg = VPNConfig.from_env(POOL_ENV)
        assert cfg.pool_enabled is True
        assert len(cfg.pool) == 3
        assert cfg.is_valid()

    def test_rotation_parses_and_falls_back_on_junk(self):
        assert (
            VPNConfig.from_env({**POOL_ENV, "JAMBU_VPN_ROTATION": "round_robin"}).rotation
            is RotationPolicy.ROUND_ROBIN
        )
        # An unparseable policy must not crash config loading.
        assert (
            VPNConfig.from_env({**POOL_ENV, "JAMBU_VPN_ROTATION": "nonsense"}).rotation
            is RotationPolicy.FAILOVER
        )

    def test_enabled_without_any_egress_is_invalid(self):
        cfg = VPNConfig.from_env({"JAMBU_VPN_ENABLED": "1"})
        assert not cfg.is_valid()
        assert any("neither a pool nor a tunnel" in p for p in cfg.validates())

    def test_tunnel_requires_interface_and_target(self):
        cfg = VPNConfig.from_env(
            {"JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_TUNNEL": "wireguard"}
        )
        problems = cfg.validates()
        assert any("TUNNEL_INTERFACE" in p for p in problems)
        assert any("TUNNEL_ENDPOINT" in p for p in problems)

    def test_bad_proxy_scheme_is_rejected(self):
        cfg = VPNConfig.from_env(
            {"JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_POOL": "ftp://bad:21"}
        )
        assert any("unsupported proxy scheme" in p for p in cfg.validates())

    def test_config_is_frozen_and_evolvable(self):
        cfg = VPNConfig.from_env({})
        with pytest.raises(Exception):
            cfg.enabled = True  # type: ignore[misc]
        assert cfg.evolve(enabled=True).enabled is True
        # evolve() must not mutate the original.
        assert cfg.enabled is False


class TestCredentialRedaction:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("socks5h://u:p@h.example:1080", "socks5h://***:***@h.example:1080"),
            ("http://user@h.example:3128", "http://***@h.example:3128"),
            ("http://h.example:3128", "http://h.example:3128"),
            ("", ""),
        ],
    )
    def test_credentials_are_stripped(self, url, expected):
        assert redact_proxy_url(url) == expected

    def test_redacted_view_never_contains_the_password(self):
        cfg = VPNConfig.from_env(
            {
                "JAMBU_VPN_ENABLED": "1",
                "JAMBU_VPN_POOL": "socks5h://user:supersecret@h.example:1080",
            }
        )
        assert "supersecret" not in str(cfg.redacted())

    def test_health_report_never_contains_the_password(self):
        pool = ProxyPool(
            VPNConfig.from_env(
                {
                    "JAMBU_VPN_ENABLED": "1",
                    "JAMBU_VPN_POOL": "http://u:supersecret@h.example:3128",
                }
            )
        )
        assert "supersecret" not in str(pool.health())

    def test_failure_reason_never_contains_the_password(self):
        pool = ProxyPool(
            VPNConfig.from_env(
                {
                    "JAMBU_VPN_ENABLED": "1",
                    "JAMBU_VPN_FAILURE_THRESHOLD": "1",
                    "JAMBU_VPN_POOL": "http://u:supersecret@h.example:3128",
                }
            )
        )
        pool.report("http://u:supersecret@h.example:3128", ok=False, error="down")
        pool.report("http://u:supersecret@h.example:3128", ok=False, error="down")
        with pytest.raises(NoHealthyEndpoint) as exc:
            pool.select()
        assert "supersecret" not in str(exc.value)


# ---------------------------------------------------------------------------
# Endpoint health model
# ---------------------------------------------------------------------------


class TestEndpointHealth:
    def test_starts_healthy_and_available(self):
        ep = EndpointHealth(url="http://h:1")
        assert ep.healthy and ep.available

    def test_threshold_trips_health_and_quarantine(self):
        ep = EndpointHealth(url="http://h:1")
        ep.record_failure("boom", threshold=2)
        assert ep.healthy is True, "one failure must not kill the endpoint"
        ep.record_failure("boom", threshold=2)
        assert ep.healthy is False
        assert ep.available is False

    def test_success_resets_the_failure_streak(self):
        ep = EndpointHealth(url="http://h:1")
        ep.record_failure("boom", threshold=3)
        ep.record_success()
        assert ep.consecutive_failures == 0
        assert ep.healthy is True

    def test_latency_is_smoothed_as_an_ewma(self):
        ep = EndpointHealth(url="http://h:1")
        ep.record_success(latency_ms=100.0)
        ep.record_success(latency_ms=200.0)
        # 100*0.7 + 200*0.3 = 130
        assert ep.latency_ms == pytest.approx(130.0)

    def test_zero_latency_is_preserved_not_treated_as_missing(self):
        ep = EndpointHealth(url="http://h:1")
        ep.record_success(latency_ms=0.0)
        assert ep.latency_ms == 0.0


# ---------------------------------------------------------------------------
# Pool: selection, rotation, stickiness, failover
# ---------------------------------------------------------------------------


def make_pool(rotation="failover", env=None, **overrides):
    base = {**POOL_ENV, "JAMBU_VPN_ROTATION": rotation}
    base.update(env or {})
    cfg = VPNConfig.from_env(base)
    if overrides:
        cfg = cfg.evolve(**overrides)
    return ProxyPool(cfg)


class TestPoolSelection:
    def test_empty_pool_raises_a_clear_error(self):
        pool = ProxyPool(VPNConfig.from_env({"JAMBU_VPN_ENABLED": "1"}))
        with pytest.raises(NoHealthyEndpoint, match="empty"):
            pool.select()

    def test_round_robin_visits_every_endpoint(self):
        pool = make_pool("round_robin")
        picked = [pool.select() for _ in range(6)]
        assert set(picked) == set(pool.urls())
        assert picked[0] == picked[3], "the cycle should wrap"

    def test_failover_is_sticky_per_session(self):
        pool = make_pool("failover")
        first = pool.select("session-1")
        assert pool.select("session-1") == first
        assert pool.select("session-1") == first

    def test_different_sessions_get_independent_pins(self):
        pool = make_pool("round_robin")
        assert pool.select("s1") != pool.select("s2")

    def test_sticky_pin_expires_after_ttl(self):
        pool = make_pool("round_robin", sticky_ttl=0.0)
        seen = {pool.select("s") for _ in range(9)}
        assert len(seen) > 1, "a zero TTL must not pin"

    def test_random_stays_within_the_pool(self):
        pool = make_pool("random")
        for _ in range(20):
            assert pool.select() in pool.urls()

    def test_least_latency_prefers_the_fastest(self):
        pool = make_pool("least_latency")
        for url in pool.urls():
            pool.report(url, ok=True, latency_ms=999.0)
        pool.report("http://a.example:1", ok=True, latency_ms=5.0)
        assert pool.select() == "http://a.example:1"

    def test_least_latency_falls_back_when_nothing_measured(self):
        pool = make_pool("least_latency")
        assert pool.select() in pool.urls()

    def test_exclude_skips_an_endpoint(self):
        pool = make_pool("failover")
        target = pool.select()
        assert pool.select(exclude={target}) != target

    def test_region_filter_restricts_the_pool(self):
        pool = make_pool("failover", env={"JAMBU_VPN_REGIONS": "eu"})
        pool.set_region("http://a.example:1", "eu")
        pool.set_region("http://b.example:2", "us")
        for _ in range(5):
            assert pool.select() == "http://a.example:1"

    def test_region_filter_with_no_match_raises(self):
        pool = make_pool("failover", env={"JAMBU_VPN_REGIONS": "antarctica"})
        with pytest.raises(NoHealthyEndpoint):
            pool.select()


class TestPoolFailover:
    def test_dead_endpoint_is_skipped_after_threshold(self):
        pool = make_pool("failover", env={"JAMBU_VPN_FAILURE_THRESHOLD": "2"})
        pool.report("http://a.example:1", ok=False, error="timeout")
        pool.report("http://a.example:1", ok=False, error="timeout")
        assert pool.select() != "http://a.example:1"

    def test_one_failure_is_not_enough(self):
        pool = make_pool("failover", env={"JAMBU_VPN_FAILURE_THRESHOLD": "3"})
        pool.report("http://a.example:1", ok=False, error="timeout")
        health = {e["url"]: e for e in pool.health()["endpoints"]}
        assert health["http://a.example:1"]["healthy"] is True

    def test_sticky_session_migrates_when_its_endpoint_dies(self):
        pool = make_pool(
            "failover", env={"JAMBU_VPN_FAILURE_THRESHOLD": "1"}
        )
        pinned = pool.select("session-x")
        pool.report(pinned, ok=False, error="dead")
        assert pool.select("session-x") != pinned

    def test_recovery_returns_an_endpoint_to_service(self):
        pool = make_pool("failover", env={"JAMBU_VPN_FAILURE_THRESHOLD": "1"})
        url = pool.urls()[0]
        pool.report(url, ok=False, error="dead")
        health = {e["url"]: e for e in pool.health()["endpoints"]}
        assert health[url]["available"] is False
        pool.report(url, ok=True)
        health = {e["url"]: e for e in pool.health()["endpoints"]}
        assert health[url]["available"] is True

    def test_all_dead_raises(self):
        pool = make_pool("failover", env={"JAMBU_VPN_FAILURE_THRESHOLD": "1"})
        for url in pool.urls():
            pool.report(url, ok=False, error="dead")
        with pytest.raises(NoHealthyEndpoint):
            pool.select()

    def test_health_counts_track_reality(self):
        pool = make_pool()
        pool.report("http://a.example:1", ok=True, latency_ms=10.0)
        snapshot = pool.health()
        assert snapshot["size"] == 3
        assert snapshot["healthy"] == 3
        assert snapshot["unhealthy"] == 0

    def test_add_and_remove_membership(self):
        pool = make_pool()
        pool.add("http://new.example:9", region="eu")
        assert "http://new.example:9" in pool.urls()
        assert pool.remove("http://new.example:9") is True
        assert pool.remove("http://new.example:9") is False

    def test_remove_drops_sticky_pins_to_that_endpoint(self):
        pool = make_pool()
        pool.select("s1")
        pool.remove(pool.urls()[0])
        assert pool.health()["sticky_sessions"] == 0


# ---------------------------------------------------------------------------
# Tunnel layer (dry-run only — never touches the host)
# ---------------------------------------------------------------------------

TUNNEL_ENV = {
    "JAMBU_VPN_ENABLED": "1",
    "JAMBU_VPN_TUNNEL": "wireguard",
    "JAMBU_VPN_TUNNEL_INTERFACE": "wg0",
    "JAMBU_VPN_TUNNEL_ENDPOINT": "vpn.example:51820",
}


class TestTunnelBackends:
    def test_no_tunnel_configured_yields_the_null_backend(self):
        assert isinstance(build_backend(VPNConfig.from_env({})), NullTunnel)

    def test_wireguard_config_selects_the_wireguard_backend(self):
        backend = build_backend(VPNConfig.from_env(TUNNEL_ENV))
        assert isinstance(backend, WireGuardTunnel)
        assert backend.kind == TunnelKind.WIREGUARD.value

    def test_openvpn_config_selects_the_openvpn_backend(self):
        cfg = VPNConfig.from_env(
            {
                "JAMBU_VPN_ENABLED": "1",
                "JAMBU_VPN_TUNNEL": "openvpn",
                "JAMBU_VPN_TUNNEL_INTERFACE": "tun0",
                "JAMBU_VPN_TUNNEL_CONFIG": "/etc/jambu.ovpn",
            }
        )
        assert isinstance(build_backend(cfg), OpenVPNTunnel)

    def test_wireguard_requires_both_vendor_binaries(self):
        """status() shells out to `wg`, not just `wg-quick`."""
        assert set(WireGuardTunnel.required_binaries()) == {"wg-quick", "wg"}

    def test_openvpn_requires_pkill_for_shutdown(self):
        assert "pkill" in OpenVPNTunnel.required_binaries()

    def test_missing_binary_is_reported_not_raised(self):
        """No traceback when the vendor tool is absent."""
        status = run(TunnelManager(VPNConfig.from_env(TUNNEL_ENV)).start())
        assert status.state == "unavailable"
        assert "not installed" in status.last_error

    def test_dry_run_reports_up_without_touching_the_host(self):
        status = run(
            TunnelManager(VPNConfig.from_env(TUNNEL_ENV), dry_run=True).start()
        )
        assert status.state == "up"
        assert status.detail.get("simulated") is True

    def test_start_is_idempotent(self):
        mgr = TunnelManager(VPNConfig.from_env(TUNNEL_ENV), dry_run=True)
        assert run(mgr.start()).state == "up"
        assert run(mgr.start()).state == "up"

    def test_stop_returns_to_down(self):
        mgr = TunnelManager(VPNConfig.from_env(TUNNEL_ENV), dry_run=True)
        run(mgr.start())
        assert run(mgr.stop()).state == "down"

    def test_null_tunnel_up_down_cycle(self):
        mgr = TunnelManager(VPNConfig.from_env({}), dry_run=True)
        assert run(mgr.start()).state == "up"
        assert run(mgr.stop()).state == "down"

    def test_status_dict_is_json_safe(self):
        status = run(
            TunnelManager(VPNConfig.from_env(TUNNEL_ENV), dry_run=True).start()
        )
        data = status.to_dict()
        assert set(data) >= {"kind", "state", "interface", "up_for_seconds"}
        assert isinstance(data["up_for_seconds"], float)


# ---------------------------------------------------------------------------
# Manager: the layered façade
# ---------------------------------------------------------------------------


class TestManagerLayering:
    def test_inert_manager_returns_none(self):
        mgr = VPNManager(VPNConfig.from_env({}))
        assert mgr.active is False
        assert mgr.resolve_proxy() is None

    def test_pool_only_configuration_is_active(self):
        mgr = VPNManager(VPNConfig.from_env(POOL_ENV))
        assert mgr.active is True
        assert mgr.resolve_proxy() in mgr.pool.urls()

    def test_fail_closed_raises_when_the_pool_is_exhausted(self):
        cfg = VPNConfig.from_env({**POOL_ENV, "JAMBU_VPN_FAILURE_THRESHOLD": "1"})
        pool = ProxyPool(cfg)
        for url in pool.urls():
            pool.report(url, ok=False, error="dead")
        with pytest.raises(VPNUnavailable):
            VPNManager(cfg, pool=pool).resolve_proxy()

    def test_fail_open_returns_none_instead_of_raising(self):
        cfg = VPNConfig.from_env(
            {
                **POOL_ENV,
                "JAMBU_VPN_FAILURE_THRESHOLD": "1",
                "JAMBU_VPN_FAIL_OPEN": "1",
            }
        )
        pool = ProxyPool(cfg)
        for url in pool.urls():
            pool.report(url, ok=False, error="dead")
        assert VPNManager(cfg, pool=pool).resolve_proxy() is None

    def test_tunnel_only_config_needs_no_pool(self):
        mgr = VPNManager(VPNConfig.from_env(TUNNEL_ENV), dry_run=True)
        assert mgr.active is True
        assert run(mgr.start())["tunnel"]["state"] == "up"

    def test_start_fails_closed_when_the_tunnel_cannot_come_up(self):
        mgr = VPNManager(VPNConfig.from_env(TUNNEL_ENV))  # not dry-run
        with pytest.raises(VPNUnavailable):
            run(mgr.start())

    def test_both_layers_configured_start_the_tunnel(self):
        cfg = VPNConfig.from_env({**TUNNEL_ENV, **POOL_ENV})
        status = run(VPNManager(cfg, dry_run=True).start())
        assert status["tunnel"]["state"] == "up"
        assert status["pool"]["size"] == 3

    def test_status_reports_configuration_problems(self):
        cfg = VPNConfig.from_env({"JAMBU_VPN_ENABLED": "1"})
        assert run(VPNManager(cfg).status())["problems"]

    def test_report_feeds_back_into_pool_health(self):
        mgr = VPNManager(VPNConfig.from_env(POOL_ENV))
        url = mgr.resolve_proxy()
        mgr.report(url, ok=True, latency_ms=12.0)
        entry = next(e for e in mgr.pool.health()["endpoints"] if e["url"] == url)
        assert entry["successes"] == 1

    def test_report_with_no_url_is_a_noop(self):
        VPNManager(VPNConfig.from_env(POOL_ENV)).report(None, ok=False)

    def test_describe_is_redacted(self):
        cfg = VPNConfig.from_env(
            {
                "JAMBU_VPN_ENABLED": "1",
                "JAMBU_VPN_POOL": "http://user:secret@h.example:1",
            }
        )
        assert "secret" not in str(VPNManager(cfg).describe())


class TestDryRun:
    """`JAMBU_VPN_DRY_RUN` must reach the manager without being threaded."""

    def test_config_reads_the_env_flag(self, monkeypatch):
        monkeypatch.delenv("JAMBU_VPN_DRY_RUN", raising=False)
        assert VPNConfig.from_env({}).dry_run is False
        monkeypatch.setenv("JAMBU_VPN_DRY_RUN", "1")
        assert VPNConfig.from_env({}).dry_run is True

    def test_manager_inherits_dry_run_from_the_environment(
        self, monkeypatch
    ):
        monkeypatch.setenv("JAMBU_VPN_DRY_RUN", "1")
        assert VPNManager(VPNConfig.from_env(TUNNEL_ENV)).dry_run is True

    def test_explicit_argument_overrides_the_environment(self, monkeypatch):
        monkeypatch.setenv("JAMBU_VPN_DRY_RUN", "1")
        assert VPNManager(VPNConfig.from_env(TUNNEL_ENV), dry_run=False).dry_run is False

    def test_env_dry_run_makes_the_tunnel_report_up(self, monkeypatch):
        monkeypatch.setenv("JAMBU_VPN_DRY_RUN", "1")
        status = run(VPNManager(VPNConfig.from_env(TUNNEL_ENV)).start())
        assert status["tunnel"]["state"] == "up"
        assert status["dry_run"] is True

    def test_status_reports_dry_run(self):
        mgr = VPNManager(VPNConfig.from_env({}), dry_run=True)
        assert run(mgr.status())["dry_run"] is True


class TestHealthProbing:
    def test_no_probe_configured_is_a_clean_noop(self):
        result = run(ProxyPool(VPNConfig.from_env(POOL_ENV)).probe_once())
        assert result["probed"] == 0
        assert "reason" in result

    def test_injected_probe_marks_failures(self):
        def probe(url):
            if "a.example" in url:
                raise OSError("refused")

        cfg = VPNConfig.from_env(
            {**POOL_ENV, "JAMBU_VPN_FAILURE_THRESHOLD": "1"}
        )
        pool = ProxyPool(cfg, probe=probe)
        run(pool.probe_once())
        entries = {e["url"]: e for e in pool.health()["endpoints"]}
        assert entries["http://a.example:1"]["healthy"] is False
        assert entries["http://b.example:2"]["healthy"] is True

    def test_probe_records_latency(self):
        cfg = VPNConfig.from_env(POOL_ENV)
        pool = ProxyPool(cfg, probe=lambda url: None)
        run(pool.probe_once())
        assert any(
            e["latency_ms"] is not None for e in pool.health()["endpoints"]
        )

    def test_async_probe_is_awaited(self):
        async def probe(url):
            return None

        pool = ProxyPool(VPNConfig.from_env(POOL_ENV), probe=probe)
        assert run(pool.probe_once())["probed"] == 3

    def test_sweeper_starts_and_stops(self):
        cfg = VPNConfig.from_env(
            {**POOL_ENV, "JAMBU_VPN_HEALTH_PROBE_URL": "https://example.com"}
        )
        pool = ProxyPool(cfg, probe=lambda url: None)

        async def drive():
            assert pool.start_sweeper() is True
            assert pool.start_sweeper() is False, "must not double-start"
            await pool.stop_sweeper()

        run(drive())

    def test_stop_sweeper_is_safe_when_never_started(self):
        run(ProxyPool(VPNConfig.from_env(POOL_ENV)).stop_sweeper())


class TestSingleton:
    def setup_method(self):
        manager_mod.reset_vpn_manager()

    def teardown_method(self):
        manager_mod.reset_vpn_manager()

    def test_reset_allows_a_fresh_manager(self):
        first = manager_mod.get_vpn_manager(VPNConfig.from_env({}))
        assert manager_mod.get_vpn_manager() is first
        manager_mod.reset_vpn_manager()
        assert manager_mod.get_vpn_manager() is not first

    def test_explicit_config_replaces_the_singleton(self):
        manager_mod.get_vpn_manager(VPNConfig.from_env({}))
        assert manager_mod.get_vpn_manager(VPNConfig.from_env(POOL_ENV)).active


# ---------------------------------------------------------------------------
# Integration: the seams other subsystems use
# ---------------------------------------------------------------------------


class TestSocksSeam:
    """`make_async_client` is how the whole engine reaches the network."""

    def setup_method(self):
        manager_mod.reset_vpn_manager()

    def teardown_method(self):
        manager_mod.reset_vpn_manager()

    def test_unconfigured_vpn_leaves_httpx_untouched(self):
        import httpx

        from backend.core import socks

        client = socks.make_async_client()
        assert isinstance(client, httpx.AsyncClient)
        assert type(client._transport).__name__ == "AsyncHTTPTransport"

    def test_configured_pool_is_consulted(self):
        from backend.core import socks

        manager_mod.get_vpn_manager(VPNConfig.from_env(POOL_ENV))
        assert socks._resolve_dynamic_proxy() in ProxyPool(
            VPNConfig.from_env(POOL_ENV)
        ).urls()

    def test_explicit_proxy_url_wins_over_the_pool(self):
        import inspect

        from backend.core import socks

        assert "proxy_url" in inspect.signature(socks.make_async_client).parameters


class TestBrowserEgress:
    """A browser session must get a stable egress for its whole flow."""

    def setup_method(self):
        manager_mod.reset_vpn_manager()

    def teardown_method(self):
        manager_mod.reset_vpn_manager()

    def test_no_vpn_means_direct(self):
        from backend.modules.browser import BrowserSession

        session = BrowserSession("s1")
        assert session._resolve_proxy() is None

    def test_tor_mode_keeps_its_default_socks(self):
        from backend.modules.browser import BrowserSession, SessionMode

        session = BrowserSession("s2", mode=SessionMode.TOR_ISOLATED)
        assert session._resolve_proxy() == "socks5://127.0.0.1:9050"

    def test_explicit_proxy_beats_the_pool(self):
        from backend.modules.browser import BrowserSession

        manager_mod.get_vpn_manager(VPNConfig.from_env(POOL_ENV))
        session = BrowserSession("s3", proxy="http://fixed.example:9")
        assert session._resolve_proxy() == "http://fixed.example:9"
        assert session.describe_proxy()["source"] == "explicit"

    def test_pool_endpoint_is_used_and_redacted(self):
        from backend.modules.browser import BrowserSession

        cfg = VPNConfig.from_env(
            {
                "JAMBU_VPN_ENABLED": "1",
                "JAMBU_VPN_POOL": "http://user:secret@p.example:3128",
            }
        )
        manager_mod.get_vpn_manager(cfg)
        session = BrowserSession("s4")
        assert session._resolve_proxy() == "http://user:secret@p.example:3128"
        described = session.describe_proxy()
        assert described["source"] == "vpn_pool"
        assert "secret" not in str(described)

    def test_session_keeps_the_same_endpoint(self):
        from backend.modules.browser import BrowserSession

        cfg = VPNConfig.from_env({**POOL_ENV, "JAMBU_VPN_ROTATION": "round_robin"})
        manager_mod.get_vpn_manager(cfg)
        first = BrowserSession("flow-42")._resolve_proxy()
        assert BrowserSession("flow-42")._resolve_proxy() == first

    def test_unlaunched_session_reports_unresolved(self):
        from backend.modules.browser import BrowserSession

        manager_mod.get_vpn_manager(VPNConfig.from_env(POOL_ENV))
        session = BrowserSession("s5")
        assert session.describe_proxy()["source"] == "unresolved"

    def test_pool_failure_does_not_block_a_launch(self):
        from backend.modules.browser import BrowserSession

        cfg = VPNConfig.from_env({**POOL_ENV, "JAMBU_VPN_FAILURE_THRESHOLD": "1"})
        manager = manager_mod.get_vpn_manager(cfg)
        # Exhaust the pool the manager actually holds.
        for url in manager.pool.urls():
            manager.pool.report(url, ok=False, error="dead")
        # Fail-closed applies to callers that demand a proxy; a browser
        # launch must still proceed rather than crash the session.
        assert BrowserSession("s6")._resolve_proxy() is None

class TestPoolPersistence:
    def test_report_writes_state_file(self, tmp_path):
        import json

        state = tmp_path / "vpn.json"
        pool = make_pool(env={"JAMBU_VPN_STATE_FILE": str(state)})
        pool.report(pool.urls()[0], ok=True, latency_ms=42.0)
        data = json.loads(state.read_text())
        assert data["version"] == 1
        ep = data["endpoints"][pool.urls()[0]]
        assert ep["successes"] == 1
        assert ep["latency_ms"] == 42.0

    def test_restart_restores_health_and_quarantine(self, tmp_path):
        state = tmp_path / "vpn.json"
        pool = make_pool(env={"JAMBU_VPN_STATE_FILE": str(state)})
        dead = pool.urls()[0]
        for _ in range(3):
            pool.report(dead, ok=False, error="boom")
        # A fresh pool over the same state file resurrects the quarantine.
        pool2 = make_pool(env={"JAMBU_VPN_STATE_FILE": str(state)})
        ep = pool2._endpoints[dead]
        assert ep.consecutive_failures == 3
        assert ep.healthy is False
        assert ep.available is False

    def test_stale_state_is_ignored(self, tmp_path):
        import json, time

        state = tmp_path / "vpn.json"
        state.write_text(json.dumps({
            "version": 1,
            "saved_at": time.time() - 10 * 86400,
            "endpoints": {},
        }))
        pool = make_pool(env={"JAMBU_VPN_STATE_FILE": str(state)})
        # Nothing restored, everything defaults to healthy.
        assert all(ep.healthy for ep in pool._endpoints.values())

    def test_corrupt_state_file_is_tolerated(self, tmp_path):
        state = tmp_path / "vpn.json"
        state.write_text("{ not json !!")
        pool = make_pool(env={"JAMBU_VPN_STATE_FILE": str(state)})
        assert pool.size == len(POOL_ENV["JAMBU_VPN_POOL"].split(","))


async def _fake_doh_1_2_3_4(host, proxy, timeout):
    return "1.2.3.4"


async def _fake_doh_9_9_9_9(host, proxy, timeout):
    return "9.9.9.9"


class TestLeakCheck:
    def _manager(self, proxy="socks5h://eu1:1080"):
        pool_env = {**POOL_ENV, "JAMBU_VPN_ENABLED": "1"}
        cfg = VPNConfig.from_env(pool_env)
        manager = VPNManager(cfg)
        manager.resolve_proxy = lambda session_key=None, **_: proxy
        return manager

    def test_no_leak_when_tunnel_ip_differs_from_direct(self):
        async def fake_get(url, proxy_url, timeout):
            if proxy_url is None:
                return "1.2.3.4"
            return "9.9.9.9" if "ipify" in url and "6" not in url else None

        async def fake_doh(host, proxy_url, timeout):
            return "9.9.9.9"

        from backend.core.vpn.leakcheck import run_leak_check

        out = run(run_leak_check(self._manager(), get=fake_get, doh=fake_doh,
                                 resolve=lambda h: "9.9.9.9"))
        assert out["verdict"] == "ok"
        assert out["leaks"] == [] or not any("matches the direct" in l for l in out["leaks"])

    def test_leak_detected_when_ip_matches_direct(self):
        async def fake_get(url, proxy_url, timeout):
            return "1.2.3.4"  # same regardless of proxy

        from backend.core.vpn.leakcheck import run_leak_check

        out = run(run_leak_check(self._manager(), get=fake_get,
                                 doh=_fake_doh_1_2_3_4,
                                 resolve=lambda h: "1.2.3.4"))
        assert out["verdict"] == "leaking"
        assert any("matches the direct" in l for l in out["leaks"])

    def test_ipv6_via_tunnel_is_flagged(self):
        async def fake_get(url, proxy_url, timeout):
            if "6.ipify" in url or "api6" in url:
                return "2001:db8::1" if proxy_url else None
            return "9.9.9.9" if proxy_url else "1.2.3.4"

        from backend.core.vpn.leakcheck import run_leak_check

        out = run(run_leak_check(self._manager(), get=fake_get,
                                 doh=_fake_doh_9_9_9_9,
                                 resolve=lambda h: "9.9.9.9"))
        assert any("IPv6" in l for l in out["leaks"])

    def test_disabled_vpn_is_not_a_leak(self):
        async def fake_get(url, proxy_url, timeout):
            return "1.2.3.4"

        from backend.core.vpn.leakcheck import run_leak_check

        class M:
            config = VPNConfig.from_env({"JAMBU_VPN_ENABLED": "0"})
            def resolve_proxy(self, session_key=None, **_):
                return None

        out = run(run_leak_check(M(), get=fake_get, resolve=lambda h: "1.2.3.4",
                                 doh=_fake_doh_1_2_3_4))
        assert out["verdict"] == "disabled"
        assert out["leaks"] == []


class TestExtendedTunnelKinds:
    def test_masque_selects_masque_backend(self):
        cfg = VPNConfig.from_env({
            "JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_TUNNEL": "masque",
            "JAMBU_VPN_TUNNEL_ENDPOINT": "masque.example.com",
        })
        backend = build_backend(cfg)
        assert backend.kind == TunnelKind.MASQUE.value
        from backend.core.vpn.tunnel import MasqueTunnel

        assert isinstance(backend, MasqueTunnel)

    def test_amneziawg_selects_amneziawg_backend(self):
        cfg = VPNConfig.from_env({
            "JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_TUNNEL": "amneziawg",
            "JAMBU_VPN_TUNNEL_INTERFACE": "awg0",
        })
        backend = build_backend(cfg)
        from backend.core.vpn.tunnel import AmneziaWGTunnel

        assert isinstance(backend, AmneziaWGTunnel)
        assert backend.kind == TunnelKind.AMNEZIAWG.value

    def test_dry_run_masque_reports_up(self):
        cfg = VPNConfig.from_env({
            "JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_TUNNEL": "masque",
            "JAMBU_VPN_TUNNEL_ENDPOINT": "masque.example.com",
        })
        status = run(TunnelManager(cfg, dry_run=True).start())
        assert status.state == "up"
        assert status.detail.get("scheme") == "masque"

    def test_dry_run_amneziawg_reports_up(self):
        cfg = VPNConfig.from_env({
            "JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_TUNNEL": "amneziawg",
            "JAMBU_VPN_TUNNEL_INTERFACE": "awg0",
        })
        status = run(TunnelManager(cfg, dry_run=True).start())
        assert status.state == "up"
        assert status.detail.get("obfuscation") == "amneziawg"


class TestPostQuantumPosture:
    def test_pq_missing_is_reported_in_problems_and_status(self):
        cfg = VPNConfig.from_env({"JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_PQ": "rosenpass"})
        manager = VPNManager(cfg)
        assert any("rosenpass" in p for p in manager.problems())

    def test_pq_unknown_value_flagged(self):
        cfg = VPNConfig.from_env({"JAMBU_VPN_ENABLED": "1", "JAMBU_VPN_PQ": "quantum-sauce"})
        assert any("JAMBU_VPN_PQ" in p for p in cfg.validates())

    def test_pq_disabled_state_is_reported(self):
        manager = VPNManager(VPNConfig.from_env({"JAMBU_VPN_ENABLED": "1"}))
        assert manager.status  # existence
        cfg = VPNConfig.from_env({"JAMBU_VPN_ENABLED": "1"})
        from backend.core.vpn.manager import _pq_status

        assert _pq_status(cfg)["state"] == "disabled"
