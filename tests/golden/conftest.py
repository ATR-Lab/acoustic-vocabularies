"""Golden tests need the `av_sound` package and its dev group.

Run them with `uv run --project sound pytest tests/golden`. A bare `python -m pytest`
from the repository root (without the package installed) skips this directory
instead of failing on imports.
"""

import importlib.util

if importlib.util.find_spec("av_sound") is None:
    collect_ignore_glob = ["*"]
