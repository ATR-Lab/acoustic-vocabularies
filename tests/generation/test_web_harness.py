"""Shared web/test harness: uvicorn in a thread, outbound-network guard, browser smoke test."""

import asyncio
import asyncio.proactor_events
import socket

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from av_generation.netguard import OutboundNetworkError, deny_outbound, is_loopback


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return "<!doctype html><title>t</title><p id='x'>hello</p>"

    return app


def test_serve_app_on_loopback(serve_app):
    base = serve_app(_app())
    assert base.startswith("http://127.0.0.1:")
    assert httpx.get(f"{base}/ping", timeout=5).json() == {"ok": "yes"}


def test_outbound_connections_are_refused(_no_outbound_network):
    with pytest.raises(OutboundNetworkError):
        socket.create_connection(("192.0.2.1", 80), timeout=1)
    with pytest.raises(OutboundNetworkError):
        socket.getaddrinfo("example.org", 443)
    with pytest.raises(httpx.ConnectError):
        httpx.get("http://192.0.2.1:9/", timeout=1)
    assert any("192.0.2.1" in r for r in _no_outbound_network)
    _no_outbound_network.clear()  # the guard itself worked; do not fail teardown checks


def test_async_and_udp_traffic_is_refused(_no_outbound_network):
    async def connect() -> None:
        await asyncio.wait_for(asyncio.open_connection("10.255.255.1", 80), timeout=2)

    with pytest.raises(OutboundNetworkError):
        asyncio.run(connect())
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        with pytest.raises(OutboundNetworkError):
            udp.sendto(b"x", ("10.255.255.1", 9))
        udp.sendto(b"x", ("127.0.0.1", 9))
        if hasattr(udp, "sendmsg"):
            with pytest.raises(OutboundNetworkError):
                udp.sendmsg([b"x"], [], 0, ("10.255.255.1", 9))
    # The proactor loop (Windows) connects through ConnectEx, not socket.connect: its
    # `sock_connect` is guarded too (checked here on every OS).
    with socket.socket() as sock, pytest.raises(OutboundNetworkError):
        coro = asyncio.proactor_events.BaseProactorEventLoop.sock_connect(
            None, sock, ("10.255.255.1", 80)
        )
        asyncio.run(coro)
    assert sum("10.255.255.1" in r for r in _no_outbound_network) >= 3
    _no_outbound_network.clear()


def test_guard_restores_the_socket_layer():
    import asyncio.selector_events

    before = (
        socket.socket.connect,
        socket.socket.sendto,
        asyncio.selector_events.BaseSelectorEventLoop.sock_connect,
        asyncio.proactor_events.BaseProactorEventLoop.sock_connect,
    )
    with deny_outbound():
        assert socket.socket.sendto is not before[1]
    with deny_outbound():
        pass
    # The suite-wide guard is still active, so compare against the values seen on entry.
    assert (
        socket.socket.connect,
        socket.socket.sendto,
        asyncio.selector_events.BaseSelectorEventLoop.sock_connect,
        asyncio.proactor_events.BaseProactorEventLoop.sock_connect,
    ) == before


def test_allowed_host_and_helpers(_no_outbound_network):
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert is_loopback(None) and is_loopback(b"127.0.0.2")
    assert not is_loopback("10.0.0.5") and not is_loopback("example.org") and not is_loopback(3)
    with deny_outbound(allowed_hosts=["llm-host.lab"]) as refused:
        with pytest.raises(OSError):
            socket.getaddrinfo("llm-host.lab", 80)  # allowed by the guard, unknown to DNS
        assert refused == []
    # The suite-wide guard (outer) still refuses the lookup the inner guard allowed.
    assert _no_outbound_network == ["lookup llm-host.lab"]
    _no_outbound_network.clear()


@pytest.mark.browser
def test_browser_smoke(serve_app, browser_page):
    base = serve_app(_app())
    browser_page.goto(base + "/")
    assert browser_page.text_content("#x") == "hello"
