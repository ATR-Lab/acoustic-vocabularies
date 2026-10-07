"""Run an ASGI app (FastAPI) with uvicorn in a background thread.

Used by the browser and API tests of #19, #21 and #23, by the mock LLM server of #16 and
by the dry run (#22), which drives the real servers with bot clients. Web UIs are FastAPI
apps serving plain HTML/CSS/JS from the package (no npm build, no CDN, no third-party JS;
`generation/docs/architecture.md`).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn


@contextmanager
def serve_in_thread(
    app: Any,  # noqa: ANN401 - any ASGI application
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    startup_timeout_s: float = 10.0,
    log_level: str = "warning",
) -> Iterator[str]:
    """Start `app` and yield its base URL (`http://host:port`); stop it on exit.

    `port=0` picks a free port. Bind to loopback unless a lab-network station needs it.
    """
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level, lifespan="auto")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name=f"uvicorn-{host}", daemon=True)
    thread.start()
    deadline = time.monotonic() + startup_timeout_s
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("uvicorn server stopped during startup")
        if time.monotonic() > deadline:
            server.should_exit = True
            raise TimeoutError("uvicorn server did not start in time")
        time.sleep(0.01)
    sockets = [s for srv in server.servers for s in srv.sockets]
    bound_port = sockets[0].getsockname()[1]
    try:
        yield f"http://{host}:{bound_port}"
    finally:
        server.should_exit = True
        thread.join(timeout=startup_timeout_s)
