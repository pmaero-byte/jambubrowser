"""WebSocket interception must not break the page.

A session installs request routing on every page, including a WebSocket handler,
because a socket is an out-of-band network path the allowlist has to cover. This
tests the handler against Playwright's actual shape: ``connect_to_server()`` is
*synchronous* and returns the server-side route, and once called Playwright stops
proxying on its own -- both directions must be forwarded by hand.

Before the fix, a single WebSocket on the page made every later step fail with
``'ServerWebSocketRoute' object can't be awaited``. FEA Lab opens one for its
agent bridge, so the whole app became untestable.
"""
from __future__ import annotations

import asyncio

from backend.modules.browser_page import PlaywrightPage


def run(coro):
    return asyncio.run(coro)


class FakeServerRoute:
    """The server-side half ``connect_to_server()`` returns."""

    def __init__(self):
        self.message_handler = None
        self.close_handler = None
        self.sent: list = []
        self.closed = None

    def on_message(self, handler):
        self.message_handler = handler

    def on_close(self, handler):
        self.close_handler = handler

    def send(self, message):
        self.sent.append(message)

    def close(self, code=None, reason=None):
        self.closed = (code, reason)


class FakePageRoute:
    """The page-side half handed to the handler."""

    def __init__(self, url="ws://127.0.0.1:8000/ws"):
        self.url = url
        self.server = FakeServerRoute()
        self.sent: list = []
        self.page_message_handler = None
        self.page_close_handler = None
        self.closed = None
        self.connect_calls = 0

    def connect_to_server(self):
        # Playwright: synchronous, and one-shot.
        self.connect_calls += 1
        if self.connect_calls > 1:
            raise RuntimeError("Already connected to the server")
        return self.server

    def on_message(self, handler):
        self.page_message_handler = handler

    def on_close(self, handler):
        self.page_close_handler = handler

    def send(self, message):
        self.sent.append(message)

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)


class FakeContext:
    """Just enough of a BrowserContext to install the two routers."""

    def __init__(self):
        self.websocket_handler = None
        self.request_handler = None

    async def route(self, pattern, handler):
        self.request_handler = handler

    async def route_web_socket(self, pattern, handler):
        self.websocket_handler = handler

    def on(self, event, handler):
        pass


class AllowAll:
    def __init__(self):
        self._routing_installed = False
        self._websocket_supported = False

    def decide(self, url, method="GET", kind="http"):
        return type("D", (), {"allowed": True, "reason": ""})()

    def report(self):
        return {"enforced": True}


def make_adapter():
    context = FakeContext()
    page = PlaywrightPage.__new__(PlaywrightPage)
    page._page = context
    page.network_policy = AllowAll()
    page._network = {}
    page._network_rules = []
    page._route_target = context
    page._route_installed = False
    page.telemetry = type("T", (), {"add_console": lambda *a, **k: None})()
    return page, context


class TestWebSocketHandler:
    def test_the_handler_is_installed(self):
        page, context = make_adapter()
        info = run(page.setup_network({}))
        assert info["websocket_supported"] is True
        assert context.websocket_handler is not None

    def test_an_open_socket_is_proxied_not_dropped(self):
        page, context = make_adapter()
        run(page.setup_network({}))
        ws = FakePageRoute()
        run(context.websocket_handler(ws))
        assert ws.connect_calls == 1

    def test_connect_to_server_is_called_synchronously(self):
        # Awaiting it was the bug: "object can't be awaited" broke every step.
        page, context = make_adapter()
        run(page.setup_network({}))
        ws = FakePageRoute()
        # Must not raise; a TypeError here is the regression.
        run(context.websocket_handler(ws))
        assert ws.server is not None

    def test_server_to_page_messages_are_forwarded(self):
        # Without this the server's frames never reach the page.
        page, context = make_adapter()
        run(page.setup_network({}))
        ws = FakePageRoute()
        run(context.websocket_handler(ws))
        ws.server.message_handler("hello from server")
        assert ws.sent == ["hello from server"]

    def test_page_to_server_messages_are_forwarded(self):
        # And without this the page's frames never reach the server.
        page, context = make_adapter()
        run(page.setup_network({}))
        ws = FakePageRoute()
        run(context.websocket_handler(ws))
        ws.page_message_handler("hello from page")
        assert ws.server.sent == ["hello from page"]

    def test_both_directions_run_together(self):
        page, context = make_adapter()
        run(page.setup_network({}))
        ws = FakePageRoute()
        run(context.websocket_handler(ws))
        ws.server.message_handler("down")
        ws.page_message_handler("up")
        assert ws.sent == ["down"]
        assert ws.server.sent == ["up"]

    def test_binary_frames_survive(self):
        page, context = make_adapter()
        run(page.setup_network({}))
        ws = FakePageRoute()
        run(context.websocket_handler(ws))
        ws.server.message_handler(b"\x00\x01\x02")
        assert ws.sent == [b"\x00\x01\x02"]

    def test_a_page_close_closes_the_server_side(self):
        page, context = make_adapter()
        run(page.setup_network({}))
        ws = FakePageRoute()
        run(context.websocket_handler(ws))
        ws.page_close_handler(1000, "bye")
        assert ws.server.closed == (1000, "bye")

    def test_a_blocked_socket_is_closed_with_1008(self):
        class Deny(AllowAll):
            def decide(self, url, method="GET", kind="http"):
                return type("D", (), {"allowed": False,
                                       "reason": "outside allowlist"})()

        page, context = make_adapter()
        page.network_policy = Deny()
        run(page.setup_network({}))
        ws = FakePageRoute()
        run(context.websocket_handler(ws))
        assert ws.closed == (1008, "outside allowlist")
        # Never connected: a denied socket must not reach the server.
        assert ws.connect_calls == 0

    def test_the_policy_still_sees_the_url(self):
        seen = []

        class Spy(AllowAll):
            def decide(self, url, method="GET", kind="http"):
                seen.append((url, kind))
                return super().decide(url, method, kind)

        page, context = make_adapter()
        page.network_policy = Spy()
        run(page.setup_network({}))
        run(context.websocket_handler(FakePageRoute()))
        assert seen == [("ws://127.0.0.1:8000/ws", "websocket")]
