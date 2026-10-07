"""Supporting GLMMs (#34): fallback ladder and its log format (analysis plan section 5).

Implemented here (shared contract): the ladder order and the JSON log every fit writes
(``glmm-log.schema.json``). Interface only (#34): building the model data and fitting.

Ladder: start with the stated random structure; if the optimizer does not converge or the
fit is singular (boundary), drop the random-effect correlations, then the dyad role slope,
then the participant teaching-format slope, always keeping the clustering intercepts. A
rung whose term is not in the model (e.g. Study A has no slopes) is logged as
``skipped``. If no rung is stable, report descriptive participant/dyad aggregates
(``descriptive``). Every attempt is logged in order; a model decision never changes which
outcome is primary.

Engine: R ``lme4::glmer`` (binomial, logit) through ``rbridge``, pinned in
``analysis/r/pins.dcf``. Model terms (section 5):

* Study A: fixed method, profile family, repetition, semantic family, endpoint; random
  intercepts batch, book within batch/method, participant within book, message.
* Study B: fixed role, teaching format, role x format, semantic family, repetition, visit;
  random intercepts dyad, participant within dyad, message; participant teaching-format
  slope and dyad role slope where identifiable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from .vocab import DataKind

GLMM_LOG_FORMAT: Final = "av-analysis/glmm-log"
GLMM_LOG_FORMAT_VERSION: Final = 1

Rung = Literal[
    "full",
    "no_correlations",
    "no_dyad_role_slope",
    "no_participant_teaching_slope",
    "descriptive",
]
LADDER: Final[tuple[Rung, ...]] = (
    "full",
    "no_correlations",
    "no_dyad_role_slope",
    "no_participant_teaching_slope",
    "descriptive",
)
AttemptStatus = Literal["accepted", "failed", "skipped"]
ATTEMPT_STATUS: Final[tuple[AttemptStatus, ...]] = ("accepted", "failed", "skipped")


@dataclass(frozen=True)
class Engine:
    """The fitting engine as reported by R at fit time."""

    name: str  # "lme4::glmer"
    r_version: str
    lme4_version: str
    optimizer: str

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "r_version": self.r_version,
            "lme4_version": self.lme4_version,
            "optimizer": self.optimizer,
        }


@dataclass(frozen=True)
class LadderAttempt:
    """One rung tried (or skipped)."""

    rung: Rung
    formula: str | None  # lme4 formula; None for descriptive and skipped rungs
    status: AttemptStatus
    converged: bool | None
    singular: bool | None
    messages: tuple[str, ...] = ()  # optimizer and check messages, verbatim
    reason: str = ""  # why the attempt failed or was skipped

    def document(self, step: int) -> dict[str, Any]:
        return {
            "step": step,
            "rung": self.rung,
            "formula": self.formula,
            "status": self.status,
            "converged": self.converged,
            "singular": self.singular,
            "messages": list(self.messages),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GlmmLog:
    """The log of one supporting model, written next to its estimates."""

    data_kind: DataKind
    study: str
    model_id: str  # e.g. "A-trained-D0", "B-W1"
    engine: Engine | None  # None when R was not run (descriptive only)
    data_sha256: str  # SHA-256 of the model data CSV sent to R
    attempts: tuple[LadderAttempt, ...] = field(default_factory=tuple)

    @property
    def final_rung(self) -> Rung:
        """The accepted rung, or ``descriptive`` when none was accepted."""
        for a in self.attempts:
            if a.status == "accepted":
                return a.rung
        return "descriptive"

    def document(self) -> dict[str, Any]:
        """JSON document (``glmm-log.schema.json``)."""
        return {
            "format": GLMM_LOG_FORMAT,
            "format_version": GLMM_LOG_FORMAT_VERSION,
            "data_kind": self.data_kind,
            "study": self.study,
            "model_id": self.model_id,
            "engine": None if self.engine is None else self.engine.document(),
            "data_sha256": self.data_sha256,
            "attempts": [a.document(i) for i, a in enumerate(self.attempts, start=1)],
            "final_rung": self.final_rung,
        }


def ladder_order_ok(attempts: Sequence[LadderAttempt]) -> bool:
    """True if attempts follow :data:`LADDER` order, stop at the first accepted rung and
    end with an accepted rung or ``descriptive``."""
    rungs = [a.rung for a in attempts]
    if rungs != list(LADDER[: len(rungs)]):
        return False
    accepted = [i for i, a in enumerate(attempts) if a.status == "accepted"]
    if accepted:
        return accepted == [len(attempts) - 1]
    return bool(rungs) and rungs[-1] == "descriptive"


# ---------------------------------------------------------------------------------------
# Interface (#34)


@dataclass(frozen=True)
class ModelSpec:
    """A supporting model: response, fixed terms, random terms per ladder rung."""

    model_id: str
    study: str
    response: str
    fixed: tuple[str, ...]
    random: Mapping[Rung, tuple[str, ...]]  # lme4 random terms at each rung


def model_specs(study: str) -> tuple[ModelSpec, ...]:
    """The prespecified supporting models of a study (section 5)."""
    raise NotImplementedError("#34: GLMM model specifications")


def fit_with_ladder(spec: ModelSpec, data_csv: bytes, *, data_kind: DataKind) -> GlmmLog:
    """Fit ``spec`` through the ladder (R via ``rbridge``) and return the full log."""
    raise NotImplementedError("#34: GLMM fit with the fallback ladder")
