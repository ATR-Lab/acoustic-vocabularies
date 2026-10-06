"""Schedules tests need the `av_schedules` package and its dev group.

Run them with `uv run --project schedules pytest tests/schedules`. A bare `python -m pytest`
from the repository root (without the package installed) skips this directory
instead of failing on imports.
"""

import importlib.util

if (
    importlib.util.find_spec("av_schedules") is None
    or importlib.util.find_spec("hypothesis") is None
    or importlib.util.find_spec("jsonschema") is None
):
    collect_ignore_glob = ["*"]
