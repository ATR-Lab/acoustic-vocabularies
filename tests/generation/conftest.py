"""Generation tests need the `av_generation` package and its dev group.

Run them with `uv run --project generation pytest --import-mode=importlib tests/generation`.
A bare `python -m pytest` from the repository root (without the package installed)
skips this directory instead of failing on imports.

Shared fixtures (implementers use these; do not redefine them in test modules):

- every test runs under `av_generation.netguard.deny_outbound()`: a connection or name
  lookup to a non-loopback host fails the call, and the test fails at teardown even if
  the code swallowed the error (the "no outbound network" rule);
- `serve_app(app) -> base_url`: run a FastAPI app with uvicorn on 127.0.0.1 for the test;
- `chromium` / `browser_page`: Playwright Chromium for tests marked `@pytest.mark.browser`.
  They skip when Chromium is not installed, unless `AV_GENERATION_BROWSER=1` (set in CI's
  browser job), where a missing browser fails. `AV_GENERATION_BROWSER_CHANNEL=chrome`
  uses an installed Google Chrome instead of Playwright's Chromium (local runs).
"""

import importlib.util
import os

import pytest

if (
    importlib.util.find_spec("av_generation") is None
    or importlib.util.find_spec("hypothesis") is None
):
    collect_ignore_glob = ["*"]
else:
    from collections.abc import Callable, Iterator
    from contextlib import ExitStack
    from typing import Any

    from av_generation.netguard import deny_outbound
    from av_generation.webserve import serve_in_thread

    def pytest_configure(config):
        config.addinivalue_line(
            "markers",
            "browser: needs Playwright Chromium (CI job 'browser'; skipped locally "
            "when Chromium is missing)",
        )

    @pytest.fixture(autouse=True)
    def _no_outbound_network() -> Iterator[list[str]]:
        with deny_outbound() as refused:
            yield refused
        assert not refused, f"outbound network attempts: {refused}"

    @pytest.fixture
    def serve_app() -> Iterator[Callable[[Any], str]]:
        with ExitStack() as stack:
            yield lambda app: stack.enter_context(serve_in_thread(app))

    @pytest.fixture(scope="session")
    def chromium():
        required = os.environ.get("AV_GENERATION_BROWSER") == "1"
        try:
            from playwright.sync_api import sync_playwright

            manager = sync_playwright().start()
        except Exception as err:  # pragma: no cover - depends on the machine
            if required:
                raise
            pytest.skip(f"Playwright unavailable: {err}")
        try:
            channel = os.environ.get("AV_GENERATION_BROWSER_CHANNEL") or None
            browser = manager.chromium.launch(channel=channel)
        except Exception as err:  # pragma: no cover - depends on the machine
            manager.stop()
            if required:
                raise
            pytest.skip(f"Chromium not installed (playwright install chromium): {err}")
        yield browser
        browser.close()
        manager.stop()

    @pytest.fixture
    def browser_page(chromium):
        context = chromium.new_context()
        page = context.new_page()
        yield page
        context.close()
