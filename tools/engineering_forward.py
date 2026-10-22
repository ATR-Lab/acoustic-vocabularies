"""Owned SSH loopback forwards for the engineering SIMULATION_TEST route (#148).

The joined engineering route reaches the remote Isaac relays through one SSH
connection. A forwarding-only `ssh -N` connection is non-interactive, so neither
OpenSSH endpoint sets TCP_NODELAY on it; small private health replies then wait
behind delayed acknowledgements (docs/isaac/private-health-timing.md). This
helper requests a pseudo-terminal running a bounded remote `sleep`, which makes
both OpenSSH endpoints mark the connection interactive (TCP_NODELAY), and turns
off OpenSSH's keystroke-timing obfuscation so interactive sends are not
quantized. Only client-side options change: no host, firewall or sshd setting,
no listener beyond 127.0.0.1, and no probe deadline or freshness bound.

This is an engineering transport aid, never a participant or deployment route
(ADR-001/ADR-003 keep each station on its own isolated network).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess

DEFAULT_FORWARDS = ((18765, 18766), (18767, 18768))  # /state, /commands


def forward_command(host: str, seconds: int, forwards=DEFAULT_FORWARDS, ssh: str = "ssh") -> list[str]:
    """Return the exact argv; validation refuses anything outside the owned route."""
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", host):
        raise ValueError("Explicit SSH host alias required (no options or user@ forms)")
    if type(seconds) is not int or not 5 <= seconds <= 3600:
        raise ValueError("Bounded forward lifetime 5..3600 seconds required")
    pairs = tuple(forwards)
    if not pairs or len({local for local, _ in pairs}) != len(pairs):
        raise ValueError("Distinct local forward ports required")
    argv = [ssh, "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "RequestTTY=force", "-o", "ObscureKeystrokeTiming=no"]
    for local, remote in pairs:
        for port in (local, remote):
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError("Explicit unprivileged ports required")
        argv += ["-L", f"127.0.0.1:{local}:127.0.0.1:{remote}"]
    # A remote command (not -N) is what makes the session interactive; it also
    # bounds the remote side if the local owner disappears.
    return argv + [host, "sleep", str(seconds)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--start", action="store_true", help="start the owned forward and print its PID")
    args = parser.parse_args()
    argv = forward_command(args.host, args.seconds)
    if not args.start:
        print(json.dumps(argv)); return
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps(dict(pid=child.pid, argv=argv)))


if __name__ == "__main__":
    main()
