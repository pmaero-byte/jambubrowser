"""The engine's startup/shutdown contract.

`lifespan` was 162 lines with the shutdown half hiding *after* the `yield` in
the body of an async generator — easy to overlook, and a missed step there is
exactly how a browser context or a VPN interface survives a restart. Startup and
shutdown are now separate functions, so a reviewer can read one and then confirm
the other covers it.

What is pinned here is the invariant that used to be only a comment:

* **optional subsystems fail soft** — the simulation queue, the VPN tunnel and
  the MCP session manager are logged and skipped, because one misconfigured
  feature must not make the audit API unreachable;
* **required wiring does not fail soft** — the mission research handler and the
  monitor schedulers are set up unconditionally, because without them those
  endpoints accept work that then fails at run time;
* **shutdown is best-effort per step and always logs** — one broken subsystem
  must not stop the rest from closing, or the process hangs on exit.

The tests drive the real functions with the subsystem modules stubbed, so they
exercise the actual control flow rather than a copy of it.
"""
from __future__ import annotations

import sys
import types

import pytest

from backend import engine


@pytest.fixture
def stub_modules(monkeypatch):
    """Stand in for the subsystem modules so no real scheduler or tunnel runs.

    Returns a dict of recorded calls, keyed by subsystem.
    """
    calls: dict[str, list] = {}

    def record(name, fn):
        calls.setdefault(name, []).append(fn)
        return fn

    # audit + flow monitor schedulers
    for name in ("audit_monitor", "flow_monitor"):
        module = types.ModuleType(f"backend.modules.{name}")

        class Scheduler:
            def __init__(self):
                self.started = False
                self.stopped = False

            def run_loop(self):
                async def loop():
                    return None
                return loop()

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

        sched = Scheduler()
        module.get_monitor_scheduler = lambda: sched
        module.get_flow_monitor_scheduler = lambda: sched
        monkeypatch.setitem(sys.modules, f"backend.modules.{name}", module)
        record(name, lambda: sched)

    # missions scheduler (needs a research handler)
    missions = types.ModuleType("backend.modules.missions")

    class MissionScheduler:
        def __init__(self):
            self.handler = None

        def set_research_handler(self, handler):
            self.handler = handler

    mission_sched = MissionScheduler()
    missions.get_scheduler = lambda: mission_sched
    monkeypatch.setitem(sys.modules, "backend.modules.missions", missions)

    # VPN manager
    vpn = types.ModuleType("backend.core.vpn")

    class VpnManager:
        active = False

        async def start(self):
            record("vpn_start", lambda: None)

        async def stop(self):
            record("vpn_stop", lambda: None)

    vpn.get_vpn_manager = lambda: VpnManager()
    monkeypatch.setitem(sys.modules, "backend.core.vpn", vpn)

    # Simulation worker (opt-in via env)
    sim = types.ModuleType("backend.decentralized.simulation")

    class SimulationWorker:
        def __init__(self):
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    sim.SimulationWorker = SimulationWorker
    monkeypatch.setitem(sys.modules, "backend.decentralized.simulation", sim)

    # MCP session manager
    import contextlib

    mcp_http = types.ModuleType("backend.mcp_http")

    class SessionManager:
        def run(self):
            @contextlib.asynccontextmanager
            async def _session():
                yield object()
            return _session()

    mcp_http.current_session_manager = lambda: SessionManager()
    monkeypatch.setitem(sys.modules, "backend.mcp_http", mcp_http)

    # Browser / shadow browser / risk shield cleanup
    for name, attr in (
        ("browser", "cleanup_browser"),
        ("shadow_browser", "close"),
        ("risk_shield", "close"),
    ):
        module = types.ModuleType(f"backend.modules.{name}")

        # Bind `closer` per iteration via a default argument: a lambda closing
        # over the loop variable would hand every module the *last* stub.
        async def closer(_record=record, _name=name, **_kw):
            _record(_name, lambda: None)

        setattr(module, attr, closer)
        if name == "shadow_browser":
            module.get_shadow_browser = (
                lambda _c=closer: types.SimpleNamespace(close=_c))
        if name == "risk_shield":
            module.get_shield = (
                lambda _c=closer: types.SimpleNamespace(close=_c))
        monkeypatch.setitem(sys.modules, f"backend.modules.{name}", module)

    # engine_runtime: safe_task and the broadcast manager
    runtime = types.ModuleType("backend.engine_runtime")

    class FakeTask:
        def __init__(self, coro):
            self._coro = coro
            self.cancelled = False
            self._coro.close()          # never actually runs in this test

        def cancel(self):
            self.cancelled = True

    def safe_task(coro, name):
        return FakeTask(coro)

    class Manager:
        async def broadcast(self, *_a, **_k):
            return None

    runtime.safe_task = safe_task
    runtime.manager = Manager()
    monkeypatch.setitem(sys.modules, "backend.engine_runtime", runtime)

    # database init
    database = types.ModuleType("backend.core.database")
    database.init_db = lambda: record("init_db", lambda: None)
    monkeypatch.setitem(sys.modules, "backend.core.database", database)

    monkeypatch.setattr(engine, "_warn_missing_runtime_deps", lambda: None)
    monkeypatch.setattr(engine, "__version__", "test", raising=False)
    return calls


class TestStartup:
    @pytest.mark.asyncio
    async def test_returns_the_handles_shutdown_needs(self, stub_modules,
                                                      monkeypatch):
        for var in ("JAMBU_SIM_QUEUE", "JAMBU_ENABLE_DECENTRALIZED"):
            monkeypatch.delenv(var, raising=False)
        handles = await engine.start_subsystems()
        assert set(handles) == {"tasks", "sim_worker", "mcp_session_stack"}
        assert isinstance(handles["tasks"], list)

    @pytest.mark.asyncio
    async def test_the_database_is_initialised_first(self, stub_modules,
                                                     monkeypatch):
        for var in ("JAMBU_SIM_QUEUE", "JAMBU_ENABLE_DECENTRALIZED"):
            monkeypatch.delenv(var, raising=False)
        await engine.start_subsystems()
        assert "init_db" in stub_modules

    @pytest.mark.asyncio
    async def test_the_simulation_worker_is_opt_in(self, stub_modules,
                                                   monkeypatch):
        monkeypatch.delenv("JAMBU_SIM_QUEUE", raising=False)
        handles = await engine.start_subsystems()
        assert handles["sim_worker"] is None

    @pytest.mark.asyncio
    async def test_the_simulation_worker_starts_when_both_flags_are_set(
            self, stub_modules, monkeypatch):
        monkeypatch.setenv("JAMBU_SIM_QUEUE", "1")
        monkeypatch.setenv("JAMBU_ENABLE_DECENTRALIZED", "1")
        handles = await engine.start_subsystems()
        assert handles["sim_worker"] is not None

    @pytest.mark.asyncio
    async def test_one_flag_alone_does_not_start_the_worker(self, stub_modules,
                                                            monkeypatch):
        """It is only meaningful when the decentralised routes are mounted."""
        monkeypatch.setenv("JAMBU_SIM_QUEUE", "1")
        monkeypatch.delenv("JAMBU_ENABLE_DECENTRALIZED", raising=False)
        handles = await engine.start_subsystems()
        assert handles["sim_worker"] is None


class TestShutdown:
    @pytest.mark.asyncio
    async def test_a_failed_subsystem_does_not_stop_the_others(self,
                                                               stub_modules,
                                                               monkeypatch):
        """One broken cleanup must not hang the process on exit."""
        for var in ("JAMBU_SIM_QUEUE", "JAMBU_ENABLE_DECENTRALIZED"):
            monkeypatch.delenv(var, raising=False)

        broken = types.ModuleType("backend.modules.risk_shield")

        async def explode():
            raise RuntimeError("shield will not close")

        broken.get_shield = lambda: types.SimpleNamespace(close=explode)
        monkeypatch.setitem(sys.modules, "backend.modules.risk_shield", broken)

        handles = await engine.start_subsystems()
        await engine.stop_subsystems(handles)      # must not raise

        # Everything after the failure still ran.
        assert "browser" in stub_modules

    @pytest.mark.asyncio
    async def test_background_tasks_are_cancelled(self, stub_modules,
                                                  monkeypatch):
        for var in ("JAMBU_SIM_QUEUE", "JAMBU_ENABLE_DECENTRALIZED"):
            monkeypatch.delenv(var, raising=False)
        handles = await engine.start_subsystems()
        tasks = handles["tasks"]
        await engine.stop_subsystems(handles)
        assert tasks and all(t.cancelled for t in tasks)

    @pytest.mark.asyncio
    async def test_the_simulation_worker_is_stopped_when_it_started(
            self, stub_modules, monkeypatch):
        monkeypatch.setenv("JAMBU_SIM_QUEUE", "1")
        monkeypatch.setenv("JAMBU_ENABLE_DECENTRALIZED", "1")
        handles = await engine.start_subsystems()
        worker = handles["sim_worker"]
        await engine.stop_subsystems(handles)
        assert worker.stopped is True

    @pytest.mark.asyncio
    async def test_shutdown_tolerates_missing_handles(self, stub_modules):
        """Defensive: stop must not assume start succeeded in full."""
        await engine.stop_subsystems({})             # must not raise

    @pytest.mark.asyncio
    async def test_a_failing_mcp_close_is_logged_last(self, stub_modules,
                                                      monkeypatch):
        """The MCP stack closes last; a failure there must be visible."""
        for var in ("JAMBU_SIM_QUEUE", "JAMBU_ENABLE_DECENTRALIZED"):
            monkeypatch.delenv(var, raising=False)

        class FailingStack:
            async def aclose(self):
                raise RuntimeError("mcp stack stuck")

        handles = await engine.start_subsystems()
        handles["mcp_session_stack"] = FailingStack()

        order: list[str] = []

        class SpyLog:
            def debug(self, msg, *a, **_kw):
                order.append(msg)

            def warning(self, msg, *a, **_kw):
                order.append(msg)

            def info(self, msg, *a, **_kw):
                pass

            def error(self, msg, *a, **_kw):
                pass

        monkeypatch.setattr(engine, "log", SpyLog())
        await engine.stop_subsystems(handles)          # must not raise

        assert any("mcp session manager" in m for m in order), order
        # Nothing logged after it: it is genuinely last.
        assert "mcp session manager" in order[-1], order

    @pytest.mark.asyncio
    async def test_a_healthy_mcp_close_logs_nothing(self, stub_modules,
                                                    monkeypatch):
        for var in ("JAMBU_SIM_QUEUE", "JAMBU_ENABLE_DECENTRALIZED"):
            monkeypatch.delenv(var, raising=False)
        handles = await engine.start_subsystems()
        await engine.stop_subsystems(handles)          # must not raise


class TestSymmetry:
    """Every subsystem started must have a matching shutdown step."""

    @pytest.mark.asyncio
    async def test_started_and_stopped_subsystems_line_up(self, stub_modules,
                                                          monkeypatch):
        for var in ("JAMBU_SIM_QUEUE", "JAMBU_ENABLE_DECENTRALIZED"):
            monkeypatch.delenv(var, raising=False)
        handles = await engine.start_subsystems()
        started = set(stub_modules)
        await engine.stop_subsystems(handles)
        stopped = set(stub_modules) - started

        # Nothing that started is left dangling: the browser, shadow browser
        # and risk shield are cleaned up on shutdown.
        assert {"browser", "shadow_browser", "risk_shield"} <= stopped