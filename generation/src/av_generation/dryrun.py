"""Synthetic-panel dry run of a full batch (#22). INTERFACE ONLY in the skeleton.

Runs the real orchestrator, A2, A3 (on the LLM host; mock server here), the A1 web app
driven by a scripted bot designer and three `rater.BotRater`s, all labelled `synthetic`
(`DEMO-` IDs). Real-time timing needs the LLM GPU: **pending (hardware)**; the
accelerated run (`clock.ScaledClock`) checks the logs here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompletenessReport:
    """Output of the automated log-completeness check (#22)."""

    ok: bool
    problems: tuple[str, ...]
    counts: dict[str, int]


def check_log_completeness(run_dir: str | os.PathLike[str]) -> CompletenessReport:
    """48 commits, 576 slot records, 576 rating records per rater, 0 message plays,
    fallback events equal to the injected ones (#22)."""
    raise NotImplementedError("#22: log completeness check")
