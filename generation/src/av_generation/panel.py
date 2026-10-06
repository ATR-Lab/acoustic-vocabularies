"""Rater panel server and station page (#21). INTERFACE ONLY in the skeleton.

`create_panel_app(host, clock=...)` returns the FastAPI app that serves
`rater_protocol.WS_PATH` (one WebSocket per station), `rater_protocol.ASSET_PATH` (WAV
bytes from `host.asset_bytes`) and `rater_protocol.STATION_PAGE` (the static station
page from `STATION_STATIC_DIR`: plain HTML/CSS/JS, no third-party code). It depends only
on `panel_session.PanelSessionHost`, which the orchestrator (#20) implements; the
division of work (who writes which record, rejoin rules) is in `panel_session`.

Rules the app enforces before calling the host: every frame is validated with
`rater_protocol.parse_message(..., sender="station")` (anything else gets `error`
`E_PROTOCOL`: no free text, ratings only 1-7 and a binary comfort); a station must send
`hello` first (`kind` from the message; unknown seats get `E_UNKNOWN_RATER` and the
socket closes); `sync_request` is answered at once from the server clock; a rejoining
station gets the current slot with `rejoin=true` and no audio replay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from av_generation.clock import Clock
from av_generation.panel_session import PanelSessionHost

STATION_STATIC_DIR: Final = Path(__file__).resolve().parent / "web" / "rater"
"""Static files of the station page (created by #21)."""


def create_panel_app(host: PanelSessionHost, *, clock: Clock) -> Any:  # noqa: ANN401 - FastAPI app
    """The panel server for one session (#21)."""
    raise NotImplementedError("#21: panel server")
