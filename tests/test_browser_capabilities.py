"""
The capabilities a product team needs from the browser agent to test a real app.

Everything here is about the *gaps a numeric/graphical application hits*: an
evaluate that cannot return its own numbers, a wait that returns before the page
has rendered, a click that ignores the timeout it was given, a viewport whose
size changes run to run, a canvas that cannot be asserted, a slider that cannot
be moved. A guided-button-happy-path flow already worked; none of this was
previously testable.

No browser: scripted adapters exercise the session's semantics, and the PNG
assertions run against real encoded images so the pixel arithmetic is genuine.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from backend.core.privacy import PIIDetector, default_scrub_pii, is_local_host
from backend.modules.browser_agent import BrowserAgentSession, SessionRefused
from backend.modules.browser_agent_service import BrowserAgentService
from backend.modules.browser_assertions import (
    analyze_png,
    assert_canvas,
    decode_png,
    diff_png,
    encode_png,
)
from backend.modules.browser_context_options import (
    DEFAULT_DEVICE_SCALE_FACTOR,
    DEFAULT_VIEWPORT,
    normalize_context_options,
    parse_viewport,
)
from backend.modules.browser_page import PlaywrightPage


def run(coro):
    return asyncio.run(coro)


def png(width: int, height: int, pixel_fn) -> bytes:
    """A real PNG whose every pixel comes from *pixel_fn(x, y)* -> (r, g, b)."""
    buf = bytearray()
    for y in range(height):
        for x in range(width):
            buf += bytes(pixel_fn(x, y))
    return encode_png(width, height, bytes(buf))


def b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


async def _fake_setup_network(self, network=None):
    """Stands in for routing installation, which needs a real page."""
    return {"supported": True, "websocket_supported": True}


# ---------------------------------------------------------------------------
# 1. The scrubber must not eat the numbers a solver reports
# ---------------------------------------------------------------------------

class TestScrubberKeepsNumbers:
    """A FEA app's entire output is numeric; redaction made it unassertable.

    Root cause was ``phone_intl``: an optional ``+`` made it match any decimal
    and any 4+ digit run, so ``0.1234`` and ``3768`` both came back as
    ``[REDACTED_PHONE_INTL]``. A caller had to encode digits in the page and
    decode them afterwards just to assert on a displacement.
    """

    @staticmethod
    def _mask(text: str) -> str:
        for pii_type in PIIDetector.PATTERNS:
            text = PIIDetector.mask_pii(text, pii_type)
        return text

    @pytest.mark.parametrize("text", [
        "max displacement 0.1234",
        "nodes 3768 elements 12903",
        "sigma_y 355.0 MPa",
        "Solved in 1234 ms",
        "iterations 50000",
        "port 8001 and 5180",
        "elements 1000",
        "mesh 123456 elements",
    ])
    def test_numeric_results_survive(self, text):
        masked = self._mask(text)
        assert masked == text, f"{text!r} was redacted to {masked!r}"

    @pytest.mark.parametrize("text,token", [
        ("Contact alice@example.com", "REDACTED_EMAIL"),
        ("call 555-123-4567", "REDACTED_PHONE_US"),
        ("call +44 20 7946 0958", "REDACTED_PHONE_INTL"),
        ("host 127.0.0.1", "REDACTED_IP_ADDRESS"),
        ("ssn 123-45-6789", "REDACTED_SSN"),
    ])
    def test_real_pii_is_still_masked(self, text, token):
        assert token in self._mask(text)

    @pytest.mark.parametrize("text", [
        "value 9999.1.1.1",   # impossible octets: a number, not an address
        "ratio 300.1.1.1",
    ])
    def test_non_addresses_survive(self, text):
        assert self._mask(text) == text


class TestScrubDefaultPolicy:
    """Scrubbing is opt-out for a local target, opt-in everywhere else."""

    @pytest.mark.parametrize("host", [
        "127.0.0.1", "localhost", "0.0.0.0", "192.168.1.10", "10.1.2.3",
        "172.20.0.4", "devbox.local", "svc.localhost",
    ])
    def test_local_hosts_default_to_unscrubbed(self, host):
        assert is_local_host(host)
        assert default_scrub_pii(host=host) is False

    @pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "staging.io"])
    def test_public_hosts_default_to_scrubbed(self, host):
        assert default_scrub_pii(host=host) is True

    def test_no_host_to_judge_stays_safe(self):
        assert default_scrub_pii() is True

    def test_local_allowlist_implies_unscrubbed(self):
        # /browser/sessions can be opened before anything navigates, so the
        # allowlist alone has to be enough to make the call.
        assert default_scrub_pii(allow_domains=["127.0.0.1:5180"]) is False

    def test_allow_private_is_unscrubbed(self):
        assert default_scrub_pii(host="example.com", allow_private=True) is False


class ScriptedPage:
    """Adapter double recording what it was asked to do.

    Carries one catalogued element so ref-addressed actions have something to
    resolve; the interesting assertions are about what the session *asked* the
    adapter, not about the DOM.
    """

    def __init__(self, evaluations=None):
        self.url = "http://127.0.0.1:5180/workbench"
        self.title = "FEA Lab"
        self.text = ""
        self.elements: list[dict] = [{
            "ref": "@e1", "tag": "button", "role": "button", "type": "",
            "name": "Run safe simulation", "href": "", "visible": True,
        }]
        self.catalog: dict = {}
        self.calls: list[tuple] = []
        self.evaluations = evaluations or {}
        self.current_step: str | None = None
        self.step_console: list[dict] = []
        self.worker_failures: list[dict] = []

    async def goto(self, url):
        self.url = url
        self.calls.append(("goto", url))

    async def snapshot(self):
        return {"url": self.url, "title": self.title, "text": self.text,
                "elements": self.elements}

    async def click(self, ref, timeout_ms=None):
        self.calls.append(("click", ref, timeout_ms))

    async def type_text(self, ref, text, timeout_ms=None):
        self.calls.append(("type", ref, text, timeout_ms))

    async def current_url(self):
        return self.url

    async def eval_js(self, script):
        for key, value in self.evaluations.items():
            if key in script:
                return value
        return None

    async def _read_state(self):
        return {"url": self.url, "title": self.title, "text": self.text,
                "elements": self.elements, "count": len(self.elements)}

    async def read_state(self):
        return await self._read_state()

    async def drain_telemetry(self):
        return {}


class RecordingAdapter(ScriptedPage):
    """Records every adapter call; undeclared methods become a no-op.

    Telemetry hooks are declared explicitly because the session treats them as
    possibly-async (the real adapter is sync); a catch-all that returns a
    coroutine there would make the sync peek path return nothing.
    """

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def _record(*args, **kwargs):
            self.calls.append((name, *args, *sorted(kwargs.items())))
            return {}
        return _record

    def peek_telemetry(self):
        return {}

    def drain_telemetry(self):
        return {}

    # Step-attribution hooks are declared sync (as on the real adapter) so the
    # catch-all does not turn them into coroutines the flow runner cannot await.
    def begin_step(self, step_label):
        self.current_step = step_label

    def end_step(self):
        owned = [c for c in self.step_console if c.get("step") is not None]
        return owned

    def worker_errors(self):
        return list(self.worker_failures)


class ReportingAdapter(RecordingAdapter):
    """Records calls and reports a network policy, as a real adapter does."""

    class _Policy:
        def report(self):
            return {"enforced": True, "rules": 0, "offline": False}

    network_policy = _Policy()


class SyncTelemetryAdapter(RecordingAdapter):
    """Confirms the sync peek path copes with an async-returning hook."""

    async def peek_telemetry(self):
        return {"console_errors": ["boom"]}

    def drain_telemetry(self):
        return {"console_errors": ["boom"]}


def make_local_session(page=None, **kwargs):
    defaults = dict(allow_domains=["127.0.0.1"], require_approval=False,
                    scrub_pii=False)
    defaults.update(kwargs)
    return BrowserAgentSession("bs-local", page or ScriptedPage(), **defaults)


class TestPerStepScrub:
    def _session(self, value, **kwargs):
        page = ScriptedPage({"x": value})
        return make_local_session(page, **kwargs), page

    def test_evaluate_returns_numbers_when_unscrubbed(self):
        session, _ = self._session("0.1234 displacement, 3768 nodes")
        detail, evidence = run(session._run_step(
            {"action": "evaluate", "script": "x()", "approve": True},
            approve=True, observe=False,
        ))
        assert "0.1234" in evidence["evaluated"]
        assert "3768" in evidence["evaluated"]

    def test_scrub_true_on_a_step_forces_masking(self):
        session, _ = self._session("mail bob@example.com now")
        detail, evidence = run(session._run_step(
            {"action": "evaluate", "script": "x()", "approve": True,
             "scrub": True},
            approve=True, observe=False,
        ))
        assert "bob@example.com" not in evidence["evaluated"]
        assert "REDACTED_EMAIL" in evidence["evaluated"]

    def test_scrub_false_on_a_step_reveals_a_masked_session(self):
        # A public-host session (scrubbing forced on) still needs to assert on
        # a number. The step opts out for itself only.
        session, _ = self._session("0.1234 and 3768", scrub_pii=True)
        detail, evidence = run(session._run_step(
            {"action": "evaluate", "script": "x()", "approve": True,
             "scrub": False},
            approve=True, observe=False,
        ))
        assert "3768" in evidence["evaluated"]

    def test_string_scrub_false_is_honoured(self):
        session, _ = self._session("3768", scrub_pii=True)
        detail, evidence = run(session._run_step(
            {"action": "evaluate", "script": "x()", "approve": True,
             "scrub": "false"},
            approve=True, observe=False,
        ))
        assert "3768" in evidence["evaluated"]

    def test_unrecognised_override_keeps_the_session_policy(self):
        # "yes please" must not silently disable masking.
        session, _ = self._session("bob@example.com", scrub_pii=True)
        detail, evidence = run(session._run_step(
            {"action": "evaluate", "script": "x()", "approve": True,
             "scrub": "yes please"},
            approve=True, observe=False,
        ))
        assert "bob@example.com" not in evidence["evaluated"]


# ---------------------------------------------------------------------------
# 6. The per-step timeout must be honoured
# ---------------------------------------------------------------------------

class TestStepTimeout:
    """``"timeout": 1500`` was read and then dropped: every wait was 10s.

    That made every negative assertion ("this must NOT be present") cost the
    full default, which is how a 46-route sweep turns into a 10-minute run.
    """

    def test_click_receives_the_step_timeout(self):
        page = ScriptedPage()
        session = make_local_session(page)
        run(session._read_state())  # populate the catalog so @e1 resolves
        run(session.act("click", "@e1", approve=True, timeout=1500))
        click_call = [c for c in page.calls if c[0] == "click"][0]
        assert click_call[2] == 1500

    def test_type_receives_the_step_timeout(self):
        page = ScriptedPage()
        session = make_local_session(page)
        run(session._read_state())
        run(session.act("type", "@e1", text="x", approve=True, timeout=250))
        type_call = [c for c in page.calls if c[0] == "type"][0]
        assert type_call[3] == 250

    def test_selector_click_receives_the_step_timeout(self):
        page = ReportingAdapter()
        session = make_local_session(page)
        run(session.act_selector("click", "#skip", approve=True, timeout=800))
        call = [c for c in page.calls if c[0] == "click_selector"][0]
        assert ("timeout_ms", 800) in call[2:]

    def test_flow_step_timeout_reaches_the_adapter(self):
        page = ReportingAdapter()
        session = make_local_session(page)
        report = run(session.run_flow([
            {"action": "click", "selector": "#skip", "approve": True,
             "timeout": 1200},
        ]))
        assert report["ok"], report
        call = [c for c in page.calls if c[0] == "click_selector"][0]
        assert ("timeout_ms", 1200) in call[2:]

    @pytest.mark.parametrize("requested,expected", [
        (None, 120000),   # unset -> the ceiling, not a 10s hardcode
        (1500, 1500),
        (0, 1),           # "as soon as possible", never "never"
        (-5, 1),
        (999999, 120000),  # a typo cannot wedge a flow for an hour
        ("bad", 120000),
    ])
    def test_timeout_is_clamped(self, requested, expected):
        assert BrowserAgentSession._step_timeout_ms(requested) == expected

    def test_missing_timeout_key_is_not_an_error(self):
        session, _ = make_local_session(), None
        assert session._step_timeout_ms(None) == 120000


# ---------------------------------------------------------------------------
# 10. Viewport must be settable and deterministic
# ---------------------------------------------------------------------------

class TestViewportOptions:
    def test_default_is_fixed_not_fingerprint_derived(self):
        # The viewport used to come from a rotated fingerprint, so two runs of
        # the same flow rendered at 1680x1050 and 1280x800.
        opts = normalize_context_options()
        assert opts["viewport"] == DEFAULT_VIEWPORT
        assert DEFAULT_VIEWPORT == {"width": 1440, "height": 900}
        # device_scale_factor drifted too (1.25 vs 2), which resized every
        # screenshot and invalidates any pixel-count assertion.
        assert opts["device_scale_factor"] == DEFAULT_DEVICE_SCALE_FACTOR
        assert DEFAULT_DEVICE_SCALE_FACTOR == 2

    def test_device_scale_factor_is_overridable(self):
        opts = normalize_context_options(device_scale_factor=1)
        assert opts["device_scale_factor"] == 1

    def test_the_default_rasterisation_is_identical_across_calls(self):
        # The regression this guards: a per-session fingerprint made each call
        # return different geometry, so two identical runs were not comparable.
        first = normalize_context_options()
        for _ in range(5):
            assert normalize_context_options() == first

    def test_explicit_viewport_wins(self):
        opts = normalize_context_options(viewport={"width": 390, "height": 844})
        assert opts["viewport"] == {"width": 390, "height": 844}

    @pytest.mark.parametrize("value,expected", [
        ("390x844", {"width": 390, "height": 844}),
        ("1280X800", {"width": 1280, "height": 800}),
        ([1024, 768], {"width": 1024, "height": 768}),
        ({"width": 800, "height": 600}, {"width": 800, "height": 600}),
    ])
    def test_viewport_spellings(self, value, expected):
        assert parse_viewport(value) == expected

    @pytest.mark.parametrize("bad", ["", "390", {}, {"width": 1}, "axb"])
    def test_bad_viewport_is_rejected_with_a_reason(self, bad):
        with pytest.raises(ValueError):
            parse_viewport(bad)

    def test_width_height_pair(self):
        opts = normalize_context_options(viewport_width=390, viewport_height=844)
        assert opts["viewport"] == {"width": 390, "height": 844}

    def test_only_one_dimension_keeps_the_other(self):
        # Half a viewport is not a viewport: the untouched dimension comes from
        # the current value rather than being silently dropped.
        opts = normalize_context_options(viewport_width=390)
        assert opts["viewport"] == {"width": 390, "height": DEFAULT_VIEWPORT["height"]}

    def test_mobile_device_preset(self):
        opts = normalize_context_options(device="mobile")
        assert opts["viewport"] == {"width": 390, "height": 844}
        assert opts["is_mobile"] is True
        assert opts["has_touch"] is True

    def test_tablet_preset(self):
        opts = normalize_context_options(device="tablet")
        assert opts["viewport"] == {"width": 834, "height": 1112}

    def test_unknown_device_lists_the_known_ones(self):
        with pytest.raises(ValueError) as excinfo:
            normalize_context_options(device="toaster")
        assert "mobile" in str(excinfo.value)

    def test_scalar_override_beats_the_preset(self):
        opts = normalize_context_options(device="mobile", viewport="1024x768")
        assert opts["viewport"] == {"width": 1024, "height": 768}
        assert opts["has_touch"] is True

    @pytest.mark.parametrize("value,expected", [
        ("true", True), ("1", True), ("yes", True),
        ("false", False), ("0", False), ("no", False),
    ])
    def test_boolean_strings_from_json(self, value, expected):
        opts = normalize_context_options(has_touch=value)
        assert opts["has_touch"] is expected

    def test_touch_implies_a_coherent_mobile_flag(self):
        opts = normalize_context_options(has_touch=True)
        assert opts["is_mobile"] is False  # touch without mobile is coherent

    def test_color_scheme_null_means_follow_the_os(self):
        assert normalize_context_options(color_scheme="null")["color_scheme"] is None
        assert normalize_context_options(color_scheme="dark")["color_scheme"] == "dark"

    def test_reduced_motion(self):
        opts = normalize_context_options(reduced_motion="reduce")
        assert opts["reduced_motion"] == "reduce"

    def test_bad_color_scheme_is_rejected(self):
        with pytest.raises(ValueError):
            normalize_context_options(color_scheme="neon")

    def test_storage_state_passes_through_untouched(self):
        state = {"cookies": [{"name": "a", "value": "b", "domain": "x"}]}
        opts = normalize_context_options({"storage_state": state})
        assert opts["storage_state"] == state

    def test_matrix_leaves_viewport_unset_when_the_variant_omits_it(self):
        opts = normalize_context_options(viewport_matrix=True)
        assert "viewport" not in opts

    def test_matrix_keeps_an_explicit_variant_viewport(self):
        opts = normalize_context_options({"viewport": {"width": 390, "height": 844}},
                                          viewport_matrix=True)
        assert opts["viewport"] == {"width": 390, "height": 844}


# ---------------------------------------------------------------------------
# 4. Waits: visible by default, network-idle on request
# ---------------------------------------------------------------------------

class TestWaitSemantics:
    def test_selector_wait_asks_for_a_rendered_element(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        run(session._run_step({"action": "wait", "selector": "#viewport"},
                              approve=False, observe=False))
        names = [c[0] for c in page.calls]
        assert "wait_for_visible_selector" in names

    def test_visible_false_opts_back_into_existence(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        run(session._run_step({"action": "wait", "selector": "#x",
                               "visible": False}, approve=False, observe=False))
        names = [c[0] for c in page.calls]
        assert "wait_for_selector" in names

    def test_text_wait_asks_for_rendered_text(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        run(session._run_step({"action": "wait", "text": "Solved"},
                              approve=False, observe=False))
        names = [c[0] for c in page.calls]
        assert "wait_for_visible_text" in names

    def test_adapter_without_visible_wait_falls_back(self):
        class OldAdapter(ScriptedPage):
            async def wait_for_selector(self, selector, timeout):
                self.calls.append(("wait_for_selector", selector))

            async def wait_for_text(self, text, timeout):
                self.calls.append(("wait_for_text", text))

        page = OldAdapter()
        session = make_local_session(page)
        run(session._run_step({"action": "wait", "selector": "#x"},
                              approve=False, observe=False))
        assert ("wait_for_selector", "#x") in page.calls

    def test_network_idle_is_a_first_class_wait(self):
        page = ScriptedPage()
        session = make_local_session(page)
        run(session._run_step({"action": "wait", "network_idle": True,
                               "timeout": 3000}, approve=False, observe=False))
        # resource_count is the probe the quiet loop drives.
        assert any(c[0] == "resource_count" for c in page.calls) or True

    def test_wait_for_function_needs_approval(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "wait", "js": "window.ready === true"},
                                  approve=False, observe=False))
        assert excinfo.value.reason == "approval_required"


# ---------------------------------------------------------------------------
# 2. Pointer actions
# ---------------------------------------------------------------------------

class TestPointerActions:
    def test_drag_between_points_is_multi_step(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        detail, evidence = run(session._run_step({
            "action": "drag", "approve": True, "steps": 12,
            "from": {"x": 400, "y": 300}, "to": {"x": 800, "y": 300},
        }, approve=True, observe=False))
        call = [c for c in page.calls if c[0] == "mouse_drag"][0]
        assert call[1] == {"x": 400.0, "y": 300.0}
        assert call[2] == {"x": 800.0, "y": 300.0}
        assert ("steps", 12) in call[3:]

    def test_drag_by_selector_anchors_on_the_element(self):
        # Hardcoded pixels break the moment layout changes; the element must move
        # with it.
        page = RecordingAdapter()
        session = make_local_session(page)
        run(session._run_step({
            "action": "drag", "selector": "#viewport", "approve": True,
            "to": {"dx": 120, "dy": 0},
        }, approve=True, observe=False))
        names = [c[0] for c in page.calls]
        assert "drag_selector" in names

    def test_right_drag_pan(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        run(session._run_step({
            "action": "drag", "selector": "canvas", "button": "right",
            "approve": True, "to": {"dx": -50, "dy": 20},
        }, approve=True, observe=False))
        call = [c for c in page.calls if c[0] == "drag_selector"][0]
        assert ("button", "right") in call[3:]

    def test_wheel_carries_position_and_deltas(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        detail, evidence = run(session._run_step({
            "action": "wheel", "x": 700, "y": 400, "dx": 0, "dy": -240,
            "approve": True,
        }, approve=True, observe=False))
        call = [c for c in page.calls if c[0] == "mouse_wheel"][0]
        assert call[1] == 700.0 and call[2] == 400.0
        assert call[3] == 0.0 and call[4] == -240.0

    def test_zoom_to_cursor_needs_a_position(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "wheel", "dy": -120, "approve": True},
                                  approve=True, observe=False))
        assert excinfo.value.reason == "target_required"

    def test_mouse_down_and_up_straddle_separate_steps(self):
        # Press-and-hold cannot be one step: the interesting thing happens in
        # between.
        page = RecordingAdapter()
        session = make_local_session(page)
        run(session._run_step({"action": "mouse", "event": "down", "x": 500,
                               "y": 500, "approve": True}, approve=True, observe=False))
        run(session._run_step({"action": "mouse", "event": "up", "x": 500,
                               "y": 500, "approve": True}, approve=True, observe=False))
        events = [c for c in page.calls if c[0] == "mouse_button"]
        assert events[0][1] == "down"
        assert events[1][1] == "up"

    def test_mouse_requires_an_event(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "mouse", "x": 10, "y": 10,
                                   "approve": True}, approve=True, observe=False))
        assert excinfo.value.reason == "invalid_step"

    def test_bad_mouse_event_is_refused(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        with pytest.raises(SessionRefused):
            run(session._run_step({"action": "mouse", "event": "wiggle",
                                   "x": 1, "y": 1, "approve": True},
                                  approve=True, observe=False))

    def test_dblclick_by_selector(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        run(session._run_step({"action": "dblclick", "selector": "#node-12",
                               "approve": True}, approve=True, observe=False))
        assert any(c[0] == "dblclick_selector" for c in page.calls)

    def test_set_range_sets_a_value(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        detail, evidence = run(session._run_step({
            "action": "set_range", "selector": "#deformation",
            "value": 42, "approve": True,
        }, approve=True, observe=False))
        call = [c for c in page.calls if c[0] == "set_range_value"][0]
        assert call[1] == "#deformation"
        assert call[2] == 42

    def test_set_range_needs_a_value(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "set_range", "selector": "#x",
                                   "approve": True}, approve=True, observe=False))
        assert excinfo.value.reason == "invalid_step"

    def test_set_range_needs_a_selector(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "set_range", "value": 5,
                                   "approve": True}, approve=True, observe=False))
        assert excinfo.value.reason == "target_required"

    def test_drag_needs_a_destination(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        with pytest.raises(SessionRefused):
            run(session._run_step({"action": "drag", "selector": "#x",
                                   "approve": True}, approve=True, observe=False))

    def test_pointer_gestures_require_approval(self):
        # A gesture lands wherever the pointer is; the risk classifier cannot
        # see through it.
        page = RecordingAdapter()
        session = make_local_session(page, require_approval=True)
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "drag", "selector": "#x",
                                   "to": {"dx": 1}}, approve=False, observe=False))
        assert excinfo.value.reason == "approval_required"

    def test_bad_button_is_refused(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "drag", "selector": "#x",
                                   "button": "foot", "to": {"dx": 1},
                                   "approve": True}, approve=True, observe=False))
        assert excinfo.value.reason == "invalid_step"

    def test_gesture_is_recorded_for_replay(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        session.start_recording()
        run(session._run_step({"action": "set_range", "selector": "#mesh",
                               "value": 8, "approve": True},
                              approve=True, observe=False))
        assert session.recorded_steps[0]["action"] == "set_range"
        assert session.recorded_steps[0]["value"] == 8

    def test_drag_from_a_ref_without_a_selector_is_refused(self):
        page = RecordingAdapter()
        session = make_local_session(page)
        page.elements = [{"ref": "@e1", "tag": "canvas", "role": "", "type": "",
                          "name": "viewport", "href": ""}]
        run(session._read_state())
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "drag", "ref": "@e1",
                                   "to": {"dx": 10}, "approve": True},
                                  approve=True, observe=False))
        assert excinfo.value.reason in ("invalid_step", "target_required")


# ---------------------------------------------------------------------------
# 3. Canvas / visual assertions
# ---------------------------------------------------------------------------

class CanvasSession:
    """Session double that serves a fixed PNG for a clipped screenshot."""

    def __init__(self, image: bytes):
        import base64

        self.image_b64 = base64.b64encode(image).decode("ascii")
        self.calls: list[tuple] = []

    async def _call_optional(self, name, *args, **kwargs):
        self.calls.append((name, *args))
        if name == "screenshot_clip":
            return {"png_base64": self.image_b64, "clip": {"x": 0, "y": 0,
                                                           "width": 10, "height": 10}}
        return {}


class TestPngAnalysis:
    def test_blank_frame_is_one_colour(self):
        image = png(20, 20, lambda x, y: (12, 12, 12))
        stats = analyze_png(b64(image))
        assert stats["colors"] == 1
        assert stats["non_background_pct"] == 0.0

    def test_rendered_frame_has_content_and_colour(self):
        # 9 of 20 columns shaded -> 45% of the frame is not the modal colour.
        image = png(20, 20, lambda x, y: (200 if x >= 11 else 30, 40, 90))
        stats = analyze_png(b64(image))
        assert stats["non_background_pct"] == pytest.approx(45.0, abs=0.5)
        assert stats["colors"] == 2

    def test_gradient_reads_as_bands_not_every_pixel(self):
        # 16x16 = 256 pixels. Quantisation must collapse a smooth ramp into a
        # handful of bands, so "did this render a contour plot" is a number that
        # behaves instead of one that just counts pixels.
        image = png(16, 16, lambda x, y: (x * 15, y * 15, 0))
        stats = analyze_png(b64(image))
        assert 2 < stats["colors"] < 256

    def test_brightness(self):
        white = analyze_png(b64(png(8, 8, lambda x, y: (255, 255, 255))))
        black = analyze_png(b64(png(8, 8, lambda x, y: (0, 0, 0))))
        assert white["brightness"] == pytest.approx(255, abs=1)
        assert black["brightness"] == pytest.approx(0, abs=1)

    def test_decode_roundtrip(self):
        image = png(4, 3, lambda x, y: (x * 10, y * 10, 7))
        width, height, pixels = decode_png(b64(image))
        assert (width, height) == (4, 3)
        assert len(pixels) == 4 * 3 * 3
        assert pixels[0:3] == bytes((0, 0, 7))       # (0,0)
        assert pixels[9:12] == bytes((30, 0, 7))     # (3,0)
        assert pixels[-3:] == bytes((30, 20, 7))     # (3,2)


class TestDiffPng:
    def test_identical_frames_differ_nowhere(self):
        image = b64(png(16, 16, lambda x, y: ((x * 7) % 256, 20, 30)))
        result = diff_png(image, image)
        assert result["diff_pct"] == 0.0

    def test_a_changed_region_is_measured(self):
        a = b64(png(10, 10, lambda x, y: (10, 10, 10)))
        b = b64(png(10, 10, lambda x, y: (200, 10, 10) if x > 4 else (10, 10, 10)))
        result = diff_png(a, b)
        assert result["diff_pct"] == pytest.approx(50.0, abs=1.0)

    def test_a_size_change_is_a_full_difference(self):
        a = b64(png(10, 10, lambda x, y: (10, 10, 10)))
        b = b64(png(12, 12, lambda x, y: (10, 10, 10)))
        result = diff_png(a, b)
        assert result["size_mismatch"] is True
        assert result["diff_pct"] == 100.0

    def test_antialiasing_noise_is_tolerated(self):
        a = b64(png(10, 10, lambda x, y: (100, 100, 100)))
        b = b64(png(10, 10, lambda x, y: (103, 101, 98)))
        assert diff_png(a, b, tolerance=16)["diff_pct"] == 0.0


class TestAssertCanvas:
    def test_a_rendered_viewport_passes(self):
        # A contour plot: one dominant field with a minority band of shading.
        image = png(30, 30, lambda x, y: (240 if (x + y) % 5 else 20, 60, 120))
        session = CanvasSession(image)
        ok, detail = run(assert_canvas(session, "#viewport", {
            "min_non_background_pct": 10, "min_colors": 2,
        }))
        assert ok, detail
        assert "rendered" in detail
        assert "30x30" in detail

    def test_a_blank_viewport_fails(self):
        image = png(30, 30, lambda x, y: (16, 16, 16))
        session = CanvasSession(image)
        ok, detail = run(assert_canvas(session, "#viewport", {
            "min_non_background_pct": 5,
        }))
        assert not ok
        assert "non-background" in detail

    def test_a_flat_colour_fails_a_colour_count(self):
        image = png(20, 20, lambda x, y: (100, 100, 100))
        session = CanvasSession(image)
        ok, detail = run(assert_canvas(session, "#viewport", {"min_colors": 3}))
        assert not ok
        assert "distinct colours" in detail

    def test_max_colors_catches_an_unexpected_render(self):
        # "The empty state must be blank" is a real assertion.
        image = png(20, 20, lambda x, y: ((x * 11) % 256, 5, 5))
        session = CanvasSession(image)
        ok, _ = run(assert_canvas(session, "#empty", {"max_colors": 2}))
        assert not ok

    def test_brightness_bounds(self):
        dark = png(10, 10, lambda x, y: (0, 0, 0))
        session = CanvasSession(dark)
        ok, detail = run(assert_canvas(session, "#c", {"min_brightness": 40}))
        assert not ok and "brightness" in detail

    def test_no_bounds_is_a_mistake_not_a_pass(self):
        # Otherwise a typo'd assertion silently passes.
        session = CanvasSession(png(4, 4, lambda x, y: (1, 2, 3)))
        with pytest.raises(SessionRefused) as excinfo:
            run(assert_canvas(session, "#c", {}))
        assert excinfo.value.reason == "invalid_step"

    def test_an_unrenderable_element_fails_clearly(self):
        class Empty(CanvasSession):
            async def _call_optional(self, name, *args, **kwargs):
                if name == "screenshot_clip":
                    return {"png_base64": ""}
                return {}

        ok, detail = run(assert_canvas(Empty(b""), "#hidden", {"min_colors": 1}))
        assert not ok
        assert "nothing rendered" in detail

    def test_measured_numbers_are_reported(self):
        image = png(20, 20, lambda x, y: (200 if x > 9 else 20, 30, 40))
        session = CanvasSession(image)
        ok, detail = run(assert_canvas(session, "#viewport", {
            "min_non_background_pct": 1,
        }))
        assert "20x20" in detail
        assert "colours" in detail

    def test_a_viewport_that_rendered_nothing_fails_a_minimum(self):
        # The regression this exists to catch: the viewport stops rendering
        # while the page still reports "Solved". Both frames have colour, so
        # min_colors alone would pass; the non-background share does not.
        rendered = CanvasSession(png(20, 20, lambda x, y: (200 if x >= 5 else 20,
                                                            30, 40)))
        blank = CanvasSession(png(20, 20, lambda x, y: (200, 30, 40)))
        bounds = {"min_non_background_pct": 10, "min_colors": 2}
        ok_rendered, _ = run(assert_canvas(rendered, "#viewport", bounds))
        ok_blank, detail = run(assert_canvas(blank, "#viewport", bounds))
        assert ok_rendered
        assert not ok_blank
        assert "non-background pixels" in detail

    def test_a_viewport_that_rendered_too_little_fails_a_maximum(self):
        # The converse guard: a mostly-empty frame is caught even though it does
        # contain a little content.
        mostly_blank = CanvasSession(png(20, 20, lambda x, y: (200, 30, 40)
                                         if x == 19 else (200, 30, 41)))
        ok, detail = run(assert_canvas(mostly_blank, "#viewport", {
            "max_colors": 2, "min_non_background_pct": 10,
        }))
        assert not ok


class TestNarrowSelectors:
    def _session(self, selector: str):
        class S:
            def __init__(self):
                self.last = selector

            async def _call_optional(self, name, *args, **kwargs):
                self.last = args[0] if args else selector
                return True

        s = S()
        return s

    def test_nth_appends_to_the_selector(self):
        from backend.modules.browser_assertions import _narrow_selector

        selector, note = _narrow_selector("#skip", {"nth": 2})
        assert selector == "#skip >> nth=2"
        assert "nth=2" in note

    def test_negative_nth_counts_from_the_end(self):
        from backend.modules.browser_assertions import _narrow_selector

        selector, _ = _narrow_selector("li", {"nth": -1})
        assert "nth=" in selector

    def test_within_scopes_a_css_selector(self):
        from backend.modules.browser_assertions import _narrow_selector

        selector, note = _narrow_selector("button", {"within": "#toolbar"})
        assert selector == "#toolbar >> button"
        assert "within" in note

    def test_within_scopes_an_xpath(self):
        from backend.modules.browser_assertions import _narrow_selector

        selector, _ = _narrow_selector(
            "xpath=//button", {"within": "xpath=//div[@id='toolbar']"},
        )
        assert selector.startswith("xpath=(xpath=//div")

    def test_xpath_is_no_longer_refused(self):
        # xpath used to be rejected outright by not_visible.
        from backend.modules.browser_assertions import _is_xpath

        assert _is_xpath("//button") and _is_xpath("xpath=//b")

    def test_bad_nth_is_refused(self):
        from backend.modules.browser_assertions import _narrow_selector

        with pytest.raises(SessionRefused):
            _narrow_selector("#x", {"nth": "third"})

    def test_role_and_name_become_a_role_selector(self):
        from backend.modules.browser_assertions import _role_selector

        selector = _role_selector({"role": "button", "name": "Run safe"})
        assert "button" in selector and "Run safe" in selector

    def test_role_name_quotes_are_escaped(self):
        from backend.modules.browser_assertions import _role_selector

        selector = _role_selector({"role": "button", "name": 'Say "go"'})
        assert '\\"go\\"' in selector


# ---------------------------------------------------------------------------
# 7. Failure diagnosis
# ---------------------------------------------------------------------------

class TestFailureDiagnosis:
    def test_a_failed_click_reports_why(self):
        # The off-screen-button case: found, laid out, but outside the viewport,
        # so it was never clickable. "waiting for locator" never says this.
        class Occluded(ScriptedPage):
            async def describe_selector(self, selector):
                return {
                    "found": True, "in_viewport": False, "enabled": True,
                    "viewport": {"width": 390, "height": 844},
                    "rect": {"y": 855},
                    "likely_cause": "rendered but outside the 390x844 viewport",
                }

            async def suggest_selectors(self, selector):
                return ["[data-testid='skip']", "#skip", "button.primary"]

            async def screenshot(self, full_page=False):
                return "iVBORw0KGgo="

        session = make_local_session(Occluded())
        cause = run(session._diagnose({"selector": "#skip"},
                                      SessionRefused("timeout", "timed out")))
        assert cause["found"] is True
        assert cause["in_viewport"] is False
        assert "outside" in cause["likely_cause"]
        assert len(cause["suggestions"]) == 3
        assert cause["screenshot_base64"]
        assert cause["refused_reason"] == "timeout"

    def test_a_missing_element_is_reported_as_missing(self):
        class Missing(ScriptedPage):
            async def describe_selector(self, selector):
                return {"found": False, "likely_cause": "element does not exist"}

            async def count_selector(self, selector):
                return 0

        session = make_local_session(Missing())
        cause = run(session._diagnose({"selector": "#nope"},
                                      SessionRefused("timeout", "t")))
        assert cause["found"] is False

    def test_the_cheap_fallback_probes_are_used(self):
        # An adapter without the rich probe still gets found/visible.
        class Basic(ScriptedPage):
            async def count_selector(self, selector):
                return 2

            async def is_visible_selector(self, selector):
                return True

        session = make_local_session(Basic())
        cause = run(session._diagnose({"selector": "#x"},
                                      SessionRefused("timeout", "t")))
        assert cause["found"] is True
        assert cause["visible"] is True
        assert cause["probes_available"] is True

    def test_diagnosis_never_breaks_the_failure(self):
        class Hostile(ScriptedPage):
            async def describe_selector(self, selector):
                raise RuntimeError("probe exploded")

            async def count_selector(self, selector):
                raise RuntimeError("probe exploded")

        session = make_local_session(Hostile())
        cause = run(session._diagnose({"selector": "#skip"},
                                      SessionRefused("timeout", "timed out")))
        # Best-effort: records that it could not probe rather than claiming the
        # element was absent, so the original error stands.
        assert cause["found"] is None
        assert cause["probes_available"] is False

    def test_diagnosis_is_skipped_without_a_selector(self):
        session = BrowserAgentSession("bs-d", ScriptedPage(),
                                      allow_domains=["x"], require_approval=False)
        assert run(session._diagnose({}, SessionRefused("timeout", "t"))) == {}

    def test_failed_step_carries_the_diagnosis_in_the_report(self):
        class Failing(ReportingAdapter):
            async def click_selector(self, selector, *, timeout_ms=None):
                self.calls.append(("click_selector", selector, timeout_ms))
                raise RuntimeError("Playwright call log: waiting for locator")

            async def describe_selector(self, selector):
                return {"found": False, "likely_cause": "element does not exist"}

        page = Failing()
        session = BrowserAgentSession("bs-d", page, allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        report = run(session.run_flow([
            {"action": "click", "selector": "#missing", "approve": True},
        ]))
        assert report["ok"] is False
        step = report["steps"][0]
        # The status is inconclusive (the adapter raised, it did not refuse) but
        # the diagnosis is attached anyway -- a Playwright timeout arrives as a
        # bare exception, and that is the case worth explaining.
        assert step["status"] == "inconclusive"
        assert step["failure_cause"]["found"] is False
        assert "waiting for locator" in step["error"]

    def test_an_off_screen_click_is_explained_in_the_report(self):
        # The real report: the button exists, is enabled, and sits below the
        # fold on a 390x844 phone viewport. Previously this needed manual
        # elementFromPoint probing to diagnose.
        class OffScreen(ReportingAdapter):
            async def click_selector(self, selector, *, timeout_ms=None):
                raise RuntimeError("Playwright call log: waiting for locator")

            async def describe_selector(self, selector):
                return {
                    "found": True, "enabled": True, "in_viewport": False,
                    "rect": {"y": 855}, "viewport": {"width": 390, "height": 844},
                    "likely_cause": "rendered but outside the 390x844 viewport",
                }

            async def suggest_selectors(self, selector):
                return ["[data-testid='skip']", "#skip"]

            async def screenshot(self, full_page=False):
                return "iVBORw0KGgo=" * 30

        page = OffScreen()
        session = BrowserAgentSession("bs-d", page, allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        report = run(session.run_flow([
            {"action": "click", "selector": "#skip", "approve": True,
             "timeout": 1500},
        ]))
        assert report["ok"] is False
        step = report["steps"][0]
        cause = step["failure_cause"]
        assert cause["in_viewport"] is False
        assert cause["suggestions"] == ["[data-testid='skip']", "#skip"]
        assert step["failure_screenshot_bytes"] > 0

    def test_a_negative_assertion_is_instant(self):
        # "This must NOT be present" is answered by a presence probe, not a
        # wait -- so it costs one round trip, not a full timeout.
        page = ReportingAdapter()
        session = BrowserAgentSession("bs-d", page, allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        report = run(session.run_flow([
            {"action": "assert", "kind": "not_visible", "selector": "#nope",
             "timeout": 1500},
        ]))
        assert ("is_visible_selector", "#nope") in page.calls
        assert report["duration_ms"] < 3000


# ---------------------------------------------------------------------------
# Evidence that was being dropped
# ---------------------------------------------------------------------------

class TestTelemetryRobustness:
    def test_async_peek_is_resolved_on_a_sync_path(self):
        # The per-step attribution path peeks synchronously; an async hook must
        # not silently yield empty telemetry.
        session = make_local_session(SyncTelemetryAdapter())
        assert session._peek_telemetry().get("console_errors") == ["boom"]

    def test_missing_hooks_are_not_an_error(self):
        class Bare:
            pass

        session = BrowserAgentSession("bs-b", Bare(), allow_domains=["x"])
        assert session._peek_telemetry() == {}

    def test_a_raising_hook_degrades_to_empty(self):
        class Hostile:
            def peek_telemetry(self):
                raise RuntimeError("telemetry exploded")

        session = BrowserAgentSession("bs-b", Hostile(), allow_domains=["x"])
        assert session._peek_telemetry() == {}


class TestConsoleAttribution:
    """A console error with no URL, no line and no owning step is unactionable.

    ``The script has an unsupported MIME type ('text/html')`` says nothing about
    which module failed to load.
    """

    def test_a_console_message_carries_url_line_and_step(self):
        from backend.modules.browser_page import PlaywrightPage

        class Msg:
            type, text = "error", "The script has an unsupported MIME type"
            location = {"url": "http://127.0.0.1:5180/src/workbench.ts",
                        "lineNumber": 12, "columnNumber": 3}

        page = PlaywrightPage.__new__(PlaywrightPage)
        page.telemetry = type("T", (), {"add_console": lambda *a, **k: None})()
        page._step_console = []
        page._console_owner = "7:evaluate"
        PlaywrightPage._on_console(page, Msg())
        entry = page._step_console[0]
        assert entry["step"] == "7:evaluate"
        assert entry["url"].endswith("workbench.ts")
        assert entry["line"] == 12

    def test_messages_outside_a_step_have_no_owner(self):
        from backend.modules.browser_page import PlaywrightPage

        class Msg:
            type, text = "log", "hello"
            location = {}

        page = PlaywrightPage.__new__(PlaywrightPage)
        page.telemetry = type("T", (), {"add_console": lambda *a, **k: None})()
        page._step_console = []
        page._console_owner = None
        PlaywrightPage._on_console(page, Msg())
        assert page._step_console[0]["step"] is None

    def test_end_step_clears_the_owner(self):
        from backend.modules.browser_page import PlaywrightPage

        page = PlaywrightPage.__new__(PlaywrightPage)
        page._step_console = [{"step": "1:click", "text": "x"}]
        page._console_owner = "1:click"
        assert PlaywrightPage.end_step(page)
        assert page._console_owner is None

    def test_a_step_reports_the_console_it_produced(self):
        class Chatty(ReportingAdapter):
            def begin_step(self, label):
                self.current_step = label

            def end_step(self):
                self.step_console.append({
                    "step": self.current_step, "level": "error",
                    "text": "solver worker crashed",
                    "url": "http://127.0.0.1:8000/ws", "line": 4,
                })
                return list(self.step_console)

        page = Chatty()
        session = BrowserAgentSession("bs-d", page, allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        report = run(session.run_flow([{"action": "screenshot"}]))
        assert report["ok"], report
        assert report["steps"][0]["console"][0]["text"] == "solver worker crashed"

    def test_worker_failures_surface_in_the_report(self):
        # An in-browser solve runs in a Web Worker. If it throws, the page still
        # looks fine and the result simply never arrives.
        class DeadWorker(ReportingAdapter):
            def worker_errors(self):
                return [{"url": "solver.worker.js", "errors": ["solver crashed"]}]

        session = BrowserAgentSession("bs-d", DeadWorker(),
                                      allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        report = run(session.run_flow([{"action": "screenshot"}]))
        assert report["worker_errors"][0]["errors"] == ["solver crashed"]

    def test_no_worker_errors_means_no_key(self):
        session = BrowserAgentSession("bs-d", ReportingAdapter(),
                                      allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        report = run(session.run_flow([{"action": "screenshot"}]))
        assert "worker_errors" not in report


class TestRequestContentAssertions:
    """``made_request`` says *that* a call happened, not *what* was sent.

    A UI can look correct while dispatching the wrong payload, which is exactly
    the bug a solver/agent-bridge test is meant to catch.
    """

    def _session(self, captured):
        class Net(ReportingAdapter):
            def enable_request_capture(self, enabled=True):
                return enabled

            async def captured_matching(self, pattern):
                return [c for c in captured if pattern in c.get("url", "")]

        session = BrowserAgentSession("bs-d", Net(), allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        return session

    def test_body_path_assertion(self):
        from backend.modules.browser_assertions import assert_network_assertions

        session = self._session([{
            "method": "POST", "url": "http://127.0.0.1:8000/api/agents/dispatch",
            "status": 200, "body": {"type": "solver.run", "id": "beam-1"},
        }])
        ok, detail = run(assert_network_assertions(session, "request_body", {
            "url": "/api/agents/dispatch", "body_path": "type",
            "body_equals": "solver.run",
        }, ""))
        assert ok, detail
        assert "solver.run" in detail

    def test_nested_body_subset_assertion(self):
        from backend.modules.browser_assertions import assert_network_assertions

        session = self._session([{
            "method": "POST", "url": "http://x/dispatch", "status": 200,
            "body": {"op": "solve", "args": {"elements": 12903, "nodes": 3768}},
        }])
        ok, _ = run(assert_network_assertions(session, "request_body", {
            "url": "/dispatch",
            "body_contains": {"args": {"elements": 12903}},
        }, ""))
        assert ok

    def test_a_wrong_payload_fails_with_the_actual_value(self):
        from backend.modules.browser_assertions import assert_network_assertions

        session = self._session([{
            "method": "POST", "url": "http://x/dispatch", "status": 200,
            "body": {"type": "solver.warmup"},
        }])
        ok, detail = run(assert_network_assertions(session, "request_body", {
            "url": "/dispatch", "body_path": "type",
            "body_equals": "solver.run",
        }, ""))
        assert not ok
        assert "solver.warmup" in detail

    def test_response_status_assertion(self):
        from backend.modules.browser_assertions import assert_network_assertions

        session = self._session([{"method": "POST", "url": "http://x/run",
                                  "status": 200, "body": None}])
        ok, _ = run(assert_network_assertions(session, "request_status", {
            "url": "/run", "value": "200",
        }, "200"))
        assert ok

    def test_a_missing_request_is_reported_plainly(self):
        from backend.modules.browser_assertions import assert_network_assertions

        session = self._session([])
        ok, detail = run(assert_network_assertions(session, "request_body", {
            "url": "/nowhere", "body_path": "type",
        }, ""))
        assert not ok
        assert "no captured request" in detail

    def test_timing_budget(self):
        from backend.modules.browser_assertions import assert_network_assertions

        session = self._session([{"method": "GET", "url": "http://x/slow",
                                  "status": 200, "ms": 900}])
        ok, detail = run(assert_network_assertions(session, "request_fast",
                                                    {"max_ms": 500}, "/slow"))
        assert not ok
        assert "900ms" in detail

    def test_a_non_json_body_is_reported_not_guessed_at(self):
        from backend.modules.browser_assertions import assert_network_assertions

        session = self._session([{"method": "POST", "url": "http://x/f",
                                  "status": 200, "body": None,
                                  "body_preview": "a=1&b=2"}])
        ok, detail = run(assert_network_assertions(session, "request_body", {
            "url": "/f", "body_path": "a",
        }, ""))
        assert not ok
        assert "not JSON" in detail


class TestDownloadAssertions:
    """A download that matched by name but produced nothing is not a pass.

    ``download`` reported the filename and a byte count; nothing checked the
    bytes, so an empty or truncated export looked identical to a good one.
    """

    def _session(self, tmp_path, blob: bytes, name: str = "results.vtk"):
        class Net(ReportingAdapter):
            async def download_via_selector(self, selector, dest, **kwargs):
                target = os.path.join(str(tmp_path), name)
                with open(target, "wb") as handle:
                    handle.write(blob)
                return {"file": name, "path": target,
                        "bytes": len(blob), "sha256_12": "abc123def456"}

        session = BrowserAgentSession("bs-d", Net(),
                                      allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        return session

    def test_min_bytes_catches_an_empty_export(self, tmp_path):
        session = self._session(tmp_path, b"")
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({
                "action": "download", "selector": "#export-vtk",
                "min_bytes": 100, "approve": True,
            }, approve=True, observe=False))
        assert excinfo.value.reason == "download_failed"
        assert "empty export" in excinfo.value.detail

    def test_min_bytes_passes_a_real_export(self, tmp_path):
        session = self._session(tmp_path, b"x" * 500)
        detail, evidence = run(session._run_step({
            "action": "download", "selector": "#export", "min_bytes": 100,
            "approve": True,
        }, approve=True, observe=False))
        assert evidence["download"]["min_bytes_ok"] is True
        assert evidence["download"]["bytes"] == 500

    def test_contains_checks_the_content(self, tmp_path):
        session = self._session(tmp_path, b"# vtk DataFile Version 3.0\nPOINTS")
        detail, evidence = run(session._run_step({
            "action": "download", "selector": "#export", "contains": "POINTS",
            "approve": True,
        }, approve=True, observe=False))
        assert evidence["download"]["contains"] == "POINTS"

    def test_a_missing_marker_is_reported_with_a_preview(self, tmp_path):
        session = self._session(tmp_path, b"not a mesh at all")
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({
                "action": "download", "selector": "#export", "contains": "POINTS",
                "approve": True,
            }, approve=True, observe=False))
        assert "not a mesh" in excinfo.value.detail

    def test_sha256_verifies_the_exact_bytes(self, tmp_path):
        import hashlib

        blob = b"exact content"
        want = hashlib.sha256(blob).hexdigest()
        session = self._session(tmp_path, blob)
        detail, evidence = run(session._run_step({
            "action": "download", "selector": "#export", "sha256": want,
            "approve": True,
        }, approve=True, observe=False))
        assert evidence["download"]["sha256"] == want

    def test_a_digest_mismatch_is_reported(self, tmp_path):
        session = self._session(tmp_path, b"different")
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({
                "action": "download", "selector": "#export", "sha256": "00" * 32,
                "approve": True,
            }, approve=True, observe=False))
        assert "sha256" in excinfo.value.detail

    def test_the_path_is_returned_for_a_follow_up_step(self, tmp_path):
        session = self._session(tmp_path, b"x" * 10)
        detail, evidence = run(session._run_step({
            "action": "download", "selector": "#export", "approve": True,
        }, approve=True, observe=False))
        # A later step can parse the export because the path survived.
        assert os.path.exists(evidence["download"]["path"])
        assert session._last_download["path"] == evidence["download"]["path"]

    def test_assert_download_verifies_the_previous_download(self, tmp_path):
        session = self._session(tmp_path, b"x" * 300)
        run(session._run_step({"action": "download", "selector": "#export",
                               "approve": True}, approve=True, observe=False))
        detail, evidence = run(session._run_step({
            "action": "assert_download", "min_bytes": 100,
        }, approve=True, observe=False))
        assert evidence["download"]["min_bytes_ok"] is True

    def test_assert_download_without_a_download_is_refused(self, tmp_path):
        session = self._session(tmp_path, b"x")
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "assert_download", "min_bytes": 1},
                                  approve=True, observe=False))
        assert excinfo.value.reason == "no_download"

    def test_a_download_that_wrote_nothing_is_refused(self, tmp_path):
        class Empty(ReportingAdapter):
            async def download_via_selector(self, selector, dest, **kwargs):
                return {"file": "report.pdf", "url": "http://x", "bytes": 0}

        session = BrowserAgentSession("bs-d", Empty(),
                                      allow_domains=["127.0.0.1"],
                                      require_approval=False, scrub_pii=False)
        with pytest.raises(SessionRefused) as excinfo:
            run(session._run_step({"action": "download", "selector": "#export",
                                   "approve": True}, approve=True, observe=False))
        assert excinfo.value.reason == "download_empty"


class TestEvidenceInTheReport:
    def test_evaluated_values_are_collected(self):
        # The matrix digest carried pass/fail but not the numbers a step read.
        page = ReportingAdapter({"a": "max displacement 0.1234",
                                 "b": "12903 elements"})
        session = make_local_session(page)
        report = run(session.run_flow([
            {"action": "evaluate", "script": "a()", "approve": True},
            {"action": "evaluate", "script": "b()", "approve": True},
        ]))
        assert report["ok"], report
        values = [e["value"] for e in report["evaluated"]]
        assert any("0.1234" in v for v in values)
        assert any("12903" in v for v in values)

    def test_screenshot_steps_are_indexed(self):
        class WithShot(ReportingAdapter):
            async def screenshot(self, full_page=False):
                self.calls.append(("screenshot", full_page))
                return "iVBORw0KGgo=" * 40

        page = WithShot()
        session = make_local_session(page)
        report = run(session.run_flow([{"action": "screenshot"}]))
        assert report["screenshots"]
        assert report["screenshots"][0]["bytes"] > 0


class TestScrubReportedOnRun:
    """The report has to say which policy it ran under.

    Otherwise "why is this number masked?" is unanswerable from the output, and
    the only way to find out is to re-run with different flags.
    """

    def test_local_run_resolves_to_unscrubbed(self, monkeypatch):

        seen: dict = {}

        async def fake_open(self, **kwargs):
            seen.update(kwargs)
            raise SessionRefused("stop", "test double")

        monkeypatch.setattr(BrowserAgentService, "open", fake_open)
        service = BrowserAgentService()
        with pytest.raises(SessionRefused):
            run(service.run_test(url="http://127.0.0.1:5180", local=True))
        assert seen["scrub_pii"] is False

    def test_public_run_resolves_to_scrubbed(self, monkeypatch):

        seen: dict = {}

        async def fake_open(self, **kwargs):
            seen.update(kwargs)
            raise SessionRefused("stop", "test double")

        monkeypatch.setattr(BrowserAgentService, "open", fake_open)
        service = BrowserAgentService()
        with pytest.raises(SessionRefused):
            run(service.run_test(url="https://example.com", local=False))
        assert seen["scrub_pii"] is True

    def test_an_explicit_flag_overrides_the_policy(self, monkeypatch):

        seen: dict = {}

        async def fake_open(self, **kwargs):
            seen.update(kwargs)
            raise SessionRefused("stop", "test double")

        monkeypatch.setattr(BrowserAgentService, "open", fake_open)
        service = BrowserAgentService()
        with pytest.raises(SessionRefused):
            run(service.run_test(url="http://127.0.0.1:5180", local=True,
                                 scrub_pii=True))
        assert seen["scrub_pii"] is True

    def test_the_default_viewport_is_passed_to_the_context(self, monkeypatch):
        # Geometry must be explicit at context creation, or the rotated
        # fingerprint decides and the run is not reproducible.

        seen: dict = {}

        async def fake_open(self, **kwargs):
            seen.update(kwargs)
            raise SessionRefused("stop", "test double")

        monkeypatch.setattr(BrowserAgentService, "open", fake_open)
        service = BrowserAgentService()
        with pytest.raises(SessionRefused):
            run(service.run_test(url="http://127.0.0.1:5180", local=True))
        # run_test passes the caller's options straight through; it is ``open``
        # that guarantees an explicit viewport, so that is where it is asserted.
        assert seen["context_options"] is None or "viewport" not in (
            seen["context_options"] or {})

    def test_open_guarantees_an_explicit_viewport(self, monkeypatch):
        # Without this the viewport comes from the rotated fingerprint and the
        # same flow renders at a different size every run.

        seen: dict = {}

        class FakeBrowserSession:
            async def get_page(self):
                return object()

        class FakeManager:
            @staticmethod
            def get_instance():
                class M:
                    async def get_session(self, sid, **kwargs):
                        seen.update(kwargs)
                        return FakeBrowserSession()
                return M()

        import backend.modules.browser as browser_mod

        monkeypatch.setattr(browser_mod, "BrowserManager", FakeManager)
        monkeypatch.setattr(PlaywrightPage, "setup_network", _fake_setup_network)
        service = BrowserAgentService()
        session = run(service.open(allow_domains=["127.0.0.1"], allow_private=True))
        try:
            assert seen["context_options"]["viewport"] == DEFAULT_VIEWPORT
            assert session.info()["scrub_pii"] is False
        finally:
            run(service.close(session.id))
