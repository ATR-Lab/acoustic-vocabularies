"""Refuse outbound network connections (no call may leave the station network).

`deny_outbound()` patches the socket layer for the current process: connections and
name lookups are allowed only for loopback hosts (and Unix sockets); anything else raises
`OutboundNetworkError` and is recorded. The generation test suite runs every test under
it (`tests/generation/conftest.py`), which is the "no outbound connection" test of #16.
`allowed_hosts` lets a run admit the lab-network LLM host explicitly.
"""

from __future__ import annotations

import ipaddress
import socket
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


@contextmanager
def deny_outbound(allowed_hosts: Iterable[str] = ()) -> Iterator[list[str]]:
    """Patch `socket` so only loopback (and `allowed_hosts`) can be reached.

    Yields the list of refused attempts (`host:port` strings).
    """
    allowed = {h.casefold() for h in allowed_hosts}
    refused: list[str] = []

    def ok(host: object) -> bool:
        return is_loopback(host) or (isinstance(host, str) and host.casefold() in allowed)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def _check(sock: socket.socket, address: Any) -> None:  # noqa: ANN401 - socket address
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return
        host = address[0] if isinstance(address, tuple) and address else address
        if not ok(host):
            port = address[1] if isinstance(address, tuple) and len(address) > 1 else "?"
            refused.append(f"{host}:{port}")
            raise OutboundNetworkError(f"outbound connection to {host}:{port} refused")

    def connect(self: socket.socket, address: Any) -> None:  # noqa: ANN401
        _check(self, address)
        original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:  # noqa: ANN401
        _check(self, address)
        return original_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if not (ok(host) or _is_ip_literal(host)):
            refused.append(f"lookup {host}")
            raise OutboundNetworkError(f"name lookup of {host!r} refused")
        return original_getaddrinfo(host, *args, **kwargs)

    with _LOCK:
        socket.socket.connect = connect  # type: ignore[method-assign, assignment]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign, assignment]
        socket.getaddrinfo = getaddrinfo
    try:
        yield refused
    finally:
        with _LOCK:
            socket.socket.connect = original_connect  # type: ignore[method-assign]
            socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
            socket.getaddrinfo = original_getaddrinfo
