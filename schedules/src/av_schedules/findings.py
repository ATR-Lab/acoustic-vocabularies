"""Check findings: one broken rule, located by unit, person and visit (#32).

Every check of the schedule validation suite (``av_schedules.checks``) and the visit
schedule check of #30 (``orders.visit_schedule_findings``) reports ``Finding`` values.
``str(finding)`` is ``"<unit> <person> <visit>: <rule>: <detail>"``; ``-`` stands for
"not applicable" (set-level findings use the set prefix such as ``A-C`` as the unit).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

NA: Final = "-"


@dataclass(frozen=True, order=True)
class Finding:
    """One broken rule. Fields contain no spaces except ``detail``."""

    unit: str
    person: str
    visit: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.unit} {self.person} {self.visit}: {self.rule}: {self.detail}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def format_findings(findings: Iterable[Finding], *, limit: int = 200) -> str:
    """Markdown table of findings (at most ``limit`` rows, then a count of the rest)."""
    rows = list(findings)
    if not rows:
        return "No findings: every check passed.\n"
    lines = [
        f"{len(rows)} finding(s):",
        "",
        "| unit | person | visit | rule | detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in rows[:limit]:
        cells = (f.unit, f.person, f.visit, f.rule, f.detail)
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    if len(rows) > limit:
        lines.append(f"| ... | | | | {len(rows) - limit} more |")
    return "\n".join(lines) + "\n"
