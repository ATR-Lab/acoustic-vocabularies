"""Refuse outbound network connections (no call may leave the station network).

`deny_outbound()` patches the socket layer for the current process: connections, UDP
sends and name lookups are allowed only for loopback hosts (and Unix sockets); anything
else raises `OutboundNetworkError` and is recorded. The generation test suite runs every
test under it (`tests/generation/conftest.py`), which is the "no outbound connection"
test of #16. `allowed_hosts` lets a run admit the lab-network LLM host explicitly.

Patched entry points:

- `socket.socket.connect`, `connect_ex`, `sendto`, `sendmsg` and `socket.getaddrinfo`
  (blocking clients, the selector event loop on Linux and macOS, UDP);
- `sock_connect` of both asyncio loop families (`BaseSelectorEventLoop`,
  `BaseProactorEventLoop`), which every `create_connection`, `open_connection` and
  connected `create_datagram_endpoint` goes through;
- on Windows, `IocpProactor.connect` and `IocpProactor.sendto`: the proactor loop
  connects with `ConnectEx` and sends datagrams with `WSASendTo`, which bypass
  `socket.socket` methods. IP literals skip `getaddrinfo` in asyncio, so these patches
  are what refuses an async client to a non-loopback IP there.
"""

from __future__ import annotations

import asyncio
import asyncio.proactor_events
import asyncio.selector_events
import ipaddress
import socket
import sys
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

_LOCK = threading.Lock()


class OutboundNetworkError(ConnectionRefusedError):
    """A connection or lookup to a non-loopback host was attempted."""


def is_loopback(host: object) -> bool:
    """True for `localhost`, loopback IP literals and `None` (wildcard lookups)."""
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.casefold() in {"localhost", "localhost.", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _is_ip_literal(host: object) -> bool:
    if not isinstance(host, str):
        return False
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def _host_port(address: Any) -> tuple[object, object]:  # noqa: ANN401 - socket address
    if isinstance(address, tuple) and address:
        return address[0], address[1] if len(address) > 1 else "?"
    return address, "?"


@contextmanager
def deny_outbound(allowed_hosts: Iterable[str] = ()) -> Iterator[list[str]]:
    """Patch `socket` and asyncio so only loopback (and `allowed_hosts`) can be reached.

    Yields the list of refused attempts (`host:port` strings).
    """
    allowed = {h.casefold() for h in allowed_hosts}
    refused: list[str] = []

    def ok(host: object) -> bool:
        return is_loopback(host) or (isinstance(host, str) and host.casefold() in allowed)

    def _check(sock: Any, address: Any, what: str = "connection") -> None:  # noqa: ANN401
        family = getattr(sock, "family", None)
        if family not in (socket.AF_INET, socket.AF_INET6) or address is None:
            return
        host, port = _host_port(address)
        if not ok(host):
            refused.append(f"{host}:{port}")
            raise OutboundNetworkError(f"outbound {what} to {host}:{port} refused")

    patches: list[tuple[Any, str, Any]] = []

    def patch(owner: Any, name: str, make: Any) -> None:  # noqa: ANN401
        original = getattr(owner, name, None)
        if original is None:
            return
        patches.append((owner, name, original))
        setattr(owner, name, make(original))

    def wrap_connect(original: Any) -> Any:  # noqa: ANN401
        def connect(self: socket.socket, address: Any) -> Any:  # noqa: ANN401
            _check(self, address)
            return original(self, address)

        return connect

    def wrap_sendto(original: Any) -> Any:  # noqa: ANN401
        def sendto(self: socket.socket, data: Any, *args: Any) -> Any:  # noqa: ANN401
            _check(self, args[-1] if args else None, "datagram")
            return original(self, data, *args)

        return sendto

    def wrap_sendmsg(original: Any) -> Any:  # noqa: ANN401
        def sendmsg(self: socket.socket, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            address = kwargs.get("address", args[3] if len(args) > 3 else None)
            _check(self, address, "datagram")
            return original(self, *args, **kwargs)

        return sendmsg

    def wrap_getaddrinfo(original: Any) -> Any:  # noqa: ANN401
        def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            if not (ok(host) or _is_ip_literal(host)):
                refused.append(f"lookup {host}")
                raise OutboundNetworkError(f"name lookup of {host!r} refused")
            return original(host, *args, **kwargs)

        return getaddrinfo

    def wrap_sock_connect(original: Any) -> Any:  # noqa: ANN401
        async def sock_connect(self: Any, sock: Any, address: Any) -> Any:  # noqa: ANN401
            _check(sock, address)
            return await original(self, sock, address)

        return sock_connect

    def wrap_iocp_connect(original: Any) -> Any:  # noqa: ANN401
        def connect(self: Any, conn: Any, address: Any) -> Any:  # noqa: ANN401
            _check(conn, address)
            return original(self, conn, address)

        return connect

    def wrap_iocp_sendto(original: Any) -> Any:  # noqa: ANN401
        def sendto(  # noqa: ANN202
            self: Any,  # noqa: ANN401
            conn: Any,  # noqa: ANN401
            buf: Any,  # noqa: ANN401
            flags: int = 0,
            addr: Any = None,  # noqa: ANN401
        ) -> Any:  # noqa: ANN401
            _check(conn, addr, "datagram")
            return original(self, conn, buf, flags, addr)

        return sendto

    with _LOCK:
        patch(socket.socket, "connect", wrap_connect)
        patch(socket.socket, "connect_ex", wrap_connect)
        patch(socket.socket, "sendto", wrap_sendto)
        patch(socket.socket, "sendmsg", wrap_sendmsg)
        patch(socket, "getaddrinfo", wrap_getaddrinfo)
        patch(asyncio.selector_events.BaseSelectorEventLoop, "sock_connect", wrap_sock_connect)
        patch(asyncio.proactor_events.BaseProactorEventLoop, "sock_connect", wrap_sock_connect)
        if sys.platform == "win32":  # pragma: no cover - Windows CI leg
            import asyncio.windows_events as windows_events

            patch(windows_events.IocpProactor, "connect", wrap_iocp_connect)
            patch(windows_events.IocpProactor, "sendto", wrap_iocp_sendto)
    try:
        yield refused
    finally:
        with _LOCK:
            for owner, name, original in reversed(patches):
                setattr(owner, name, original)
