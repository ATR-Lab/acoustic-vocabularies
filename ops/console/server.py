"""Loopback-only administrative server using installed Python dependencies."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"schedules/src"))

from av_schedules.reveal import RevealLog
from .core import Audit, Console, ConsoleFault, Visit, code, encoded, load_bundle, masked, require, strict_json, utc_now
from .transport import DemoEngine, Mailbox


def demo_catalog():
    def fixture(study, visit, role=None):
        rows = tuple(dict(participant_id="demo-01", visit=visit, block=name, expected_count=str(n),
                          actual_count="", start_time="", end_time="", comfort_check="", phone_locked="",
                          hash_check="sha256:"+"c"*64, deviations="", operator_signoff="")
                     for name, n in (("trained", 3), ("atomic", 2)))
        now = utc_now()
        today = now.astimezone().date()
        anchors = {"D0": (today-timedelta(days=7)).isoformat(), "V1": today.isoformat(),
                   "V3": (today-timedelta(days=28)).isoformat(), "active": (now-timedelta(hours=1)).isoformat()}
        return Visit("demo-01", study, visit, "bk-demo", role, True, "a"*64, "b"*64, "c"*64, rows, anchors)
    return {"demo-a": lambda: fixture("A", "D7"), "demo-b-active": lambda: fixture("B", "W4", "active"),
            "demo-b-yoked": lambda: fixture("B", "W4", "yoked")}


@contextmanager
def writer_locks(audit_path, mailbox=None):
    """Both audit and real mailbox have one writer, even with different ports."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    targets = [audit_path.with_suffix(audit_path.suffix+".lock")]
    if mailbox is not None:
        targets.append(Path(mailbox)/"console-writer.lock")
    owned = []
    try:
        for path in targets:
            with path.open("x") as stream:
                stream.write("single-console-writer\n")
            owned.append(path)
        yield
    finally:
        for path in reversed(owned):
            path.unlink(missing_ok=True)


def make_server(console, port=0, demo=False, simulation=False):
    token = secrets.token_hex(32)
    static = Path(__file__).with_name("static")

    def snapshot():
        value = dict(console.snapshot(), token=token, demo_transport=demo)
        if simulation:
            value["simulation_test"] = True
        return masked(value)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *_):
            pass  # No participant IDs, notes, paths or query strings in terminal logs.

        def send(self, status, body, mime="application/json", download=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; connect-src 'self'")
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="'+download+'"')
            self.end_headers()
            self.wfile.write(body)

        def origin_check(self, mutation=False):
            origin = "http://127.0.0.1:"+str(self.server.server_port)
            require(self.headers.get("Host") == origin[7:], "origin_rejected")
            require(self.headers.get("Sec-Fetch-Site", "same-origin") not in ("cross-site",), "origin_rejected")
            if mutation:
                require(self.headers.get("Origin") == origin and
                        secrets.compare_digest(self.headers.get("X-Console-Token", ""), token), "origin_rejected")

        def do_GET(self):
            try:
                self.origin_check()
                if self.path in ("/", "/app.js", "/style.css"):
                    name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[self.path]
                    mime = {"/": "text/html; charset=utf-8", "/app.js": "text/javascript", "/style.css": "text/css"}[self.path]
                    self.send(200, (static/name).read_bytes(), mime)
                elif self.path == "/api/state":
                    self.send(200, encoded(snapshot()))
                elif self.path == "/api/run-sheet.csv":
                    require(console.visit is not None, "visit_not_loaded")
                    self.send(200, console.run_sheet(), "text/csv", "visit-run-sheet.csv")
                elif self.path == "/api/deviations.csv":
                    require(console.visit is not None, "visit_not_loaded")
                    self.send(200, console.deviation_csv(), "text/csv", "provisional-deviations.csv")
                else:
                    self.send(404, encoded(dict(error="not_found")))
            except ConsoleFault as fault:
                self.send(409, encoded(dict(error=str(fault))))
            except Exception:
                self.send(500, encoded(dict(error="request_failed")))

        def do_POST(self):
            try:
                self.origin_check(True)
                require(self.path == "/api/command" and self.headers.get("Content-Type") == "application/json", "request_invalid")
                count = int(self.headers.get("Content-Length", "0"))
                require(0 < count <= 16384 and "Transfer-Encoding" not in self.headers, "request_invalid")
                body = strict_json(self.rfile.read(count))
                require(isinstance(body, dict) and set(body) == {"action", "staff", "payload"}, "request_invalid")
                staff = code(body["staff"])
                if body["action"] == "load":
                    require(isinstance(body["payload"], dict) and set(body["payload"]) == {"visit"}, "request_invalid")
                    console.load(body["payload"]["visit"], staff)
                elif body["action"] == "demo_fault":
                    require(demo and isinstance(console.engine, DemoEngine) and set(body["payload"]) == {"fault"}, "request_invalid")
                    require(body["payload"]["fault"] in (None, "bridge_stale", "headset_unavailable", "frame_freeze"), "request_invalid")
                    console.engine.fault = body["payload"]["fault"]
                else:
                    console.command(body["action"], staff, body["payload"])
                self.send(200, encoded(snapshot()))
            except ConsoleFault as fault:
                self.send(409, encoded(dict(error=str(fault))))
            except Exception:
                self.send(400, encoded(dict(error="request_failed")))

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1
    return server


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--demo", action="store_true")
    group.add_argument("--config", type=Path)
    group.add_argument("--simulation-config", type=Path)
    parser.add_argument("--simulation-config-sha256")
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--protocol", default="engineering-pending-review")
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()
    if args.demo:
        catalog, engine = demo_catalog(), DemoEngine()
        mailbox = None
    elif args.simulation_config:
        from .simulation import load_catalog
        catalog, mailbox = load_catalog(args.simulation_config, args.simulation_config_sha256, args.audit, args.protocol)
        mailbox.mkdir(parents=True, exist_ok=True)
        engine = Mailbox(mailbox)
    else:
        config = strict_json(args.config.read_bytes())
        require(set(config) == {"mailbox", "visits"} and isinstance(config["visits"], dict), "config_invalid")
        catalog = {}
        for alias, entry in config["visits"].items():
            code(alias)
            def load(entry=entry):
                reveals = RevealLog(Path(entry["allocation_list"]), Path(entry["reveal_log"]))
                return load_bundle(entry, reveals)
            catalog[alias] = load
        engine = Mailbox(config["mailbox"])
        mailbox = config["mailbox"]
    # Read/replay only after both writer locks are held.
    with writer_locks(args.audit, mailbox):
        audit = Audit(args.audit, args.protocol)
        server = make_server(Console(catalog, engine, audit), args.port, args.demo, bool(args.simulation_config))
        print(f"Operator console: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
