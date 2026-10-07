"""FastAPI app of the A1 designer interface (#19; public entry point `a1.create_a1_app`).

Serves the routes of `a1.ROUTES` plus the page assets under `/a1/static/` from
`web/a1/`. Every response carries `Cache-Control: no-store` (audio cannot come back from
a cache) and a same-origin Content-Security-Policy (no third-party code, no frames, no
media elements). Request bodies are strict JSON of at most 64 KiB; a malformed body is a
client error and consumes nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

from av_sound.recipe import StrictJsonError, strict_json_loads
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from av_generation.a1 import A1Error, A1SlotService

STATIC_DIR: Final = Path(__file__).resolve().parent / "web" / "a1"
"""The page, script and stylesheet of the designer screen."""
STATIC_FILES: Final[dict[str, str]] = {
    "a1.js": "text/javascript; charset=utf-8",
    "a1.css": "text/css; charset=utf-8",
}
MAX_BODY_BYTES: Final = 65_536
SECURITY_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def _error(err: A1Error) -> JSONResponse:
    return JSONResponse(err.to_dict(), status_code=err.status)


async def _json_body(request: Request) -> dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise A1Error("E_TOO_LARGE", 413, f"request bodies are limited to {MAX_BODY_BYTES} bytes")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise A1Error("E_TOO_LARGE", 413, f"request bodies are limited to {MAX_BODY_BYTES} bytes")
    if not body.strip():
        return {}
    try:
        data = strict_json_loads(body)
    except (StrictJsonError, UnicodeDecodeError) as err:
        raise A1Error("E_BAD_REQUEST", 400, f"the body is not strict JSON ({err})") from err
    if not isinstance(data, dict):
        raise A1Error("E_BAD_REQUEST", 400, "the body must be a JSON object")
    return data


def build_app(service: A1SlotService) -> FastAPI:
    """The A1 app for `service` (see `a1.create_a1_app`)."""
    app = FastAPI(
        title="A1 designer interface",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.a1_service = service

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.exception_handler(A1Error)
    async def a1_error(request: Request, err: A1Error) -> JSONResponse:
        return _error(err)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/a1/", status_code=307)

    @app.get("/a1", include_in_schema=False)
    def page_redirect() -> RedirectResponse:
        return RedirectResponse("/a1/", status_code=307)

    @app.get("/a1/")
    def page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/a1/static/{name}")
    def static(name: str) -> FileResponse:
        media = STATIC_FILES.get(name)
        if media is None:
            raise A1Error("E_NOT_FOUND", 404, "no such file")
        return FileResponse(STATIC_DIR / name, media_type=media)

    @app.get("/a1/api/state")
    def state() -> dict[str, Any]:
        return service.state()

    @app.post("/a1/api/slots/open")
    def open_slot() -> dict[str, Any]:
        return service.open_slot()

    @app.post("/a1/api/slots/{slot_id}/submit")
    async def submit(slot_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        if "recipe" not in body:
            raise A1Error("E_BAD_REQUEST", 400, "the body needs a 'recipe' field")
        result: dict[str, Any] = await run_in_threadpool(service.submit, slot_id, body["recipe"])
        return result

    @app.get("/a1/api/audio/{token}")
    def audio(token: str) -> Response:
        data = service.audio(token)
        return Response(content=data, media_type="audio/wav")

    @app.get("/a1/api/feedback")
    def feedback() -> dict[str, Any]:
        return service.feedback()

    @app.get("/a1/api/book")
    def book() -> dict[str, Any]:
        return service.book()

    @app.post("/a1/api/activity")
    async def activity(request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        kind = body.get("kind")
        if not isinstance(kind, str):
            raise A1Error("E_BAD_ACTIVITY", 400, "the body needs a 'kind' string")
        result: dict[str, Any] = await run_in_threadpool(service.activity, kind)
        return result

    return app
