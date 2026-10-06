"""Supporting GLMMs (#34): fallback ladder and its log format (analysis plan section 5).

Implemented here: the ladder order and the JSON log every fit writes
(``glmm-log.schema.json``; shared contract), the model specifications, the model data
and the fit through the ladder (#34).

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

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from .fileio import csv_bytes, sha256_bytes
from .scoring import ScoredTrial
from .unmask import Conditions
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
# Model specifications, model data and the ladder (#34)

RSCRIPT: Final = "glmm.R"
OPTIMIZER: Final = "bobyqa"
MAX_FUN: Final = 200_000
SINGULAR_TOL: Final = 1e-4  # lme4::isSingular default
REQUEST_FORMAT: Final = "av-analysis/glmm-request"
Runner = Callable[[str, Mapping[str, Any], Mapping[str, bytes]], Mapping[str, Any]]

_A_RANDOM: Final[tuple[str, ...]] = (
    "(1 | unit_id)",
    "(1 | book_id)",
    "(1 | person_id)",
    "(1 | item_id)",
)
_B_RANDOM: Final[dict[Rung, tuple[str, ...]]] = {
    "full": ("(1 + role_c | unit_id)", "(1 + format_c | person_id)", "(1 | item_id)"),
    "no_correlations": ("(1 + role_c || unit_id)", "(1 + format_c || person_id)", "(1 | item_id)"),
    "no_dyad_role_slope": ("(1 | unit_id)", "(1 + format_c || person_id)", "(1 | item_id)"),
    "no_participant_teaching_slope": ("(1 | unit_id)", "(1 | person_id)", "(1 | item_id)"),
}
_A_FACTORS: Final[dict[str, tuple[str, ...]]] = {
    "method": ("A2", "A1", "A3"),
    "profile": ("P1", "P2", "P3"),
    "rep": ("1", "2"),
    "family": ("K", "Q"),
    "endpoint": ("D0", "D7"),
}
_B_FACTORS: Final[dict[str, tuple[str, ...]]] = {
    "family": ("K", "Q"),
    "rep": ("1", "2"),
    "visit": ("W1", "W4"),
}


@dataclass(frozen=True)
class ModelSpec:
    """A supporting model: response, fixed terms, random terms per ladder rung."""

    model_id: str
    study: str
    response: str
    fixed: tuple[str, ...]
    random: Mapping[Rung, tuple[str, ...]]  # lme4 random terms at each rung
    factors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # first = reference
    numeric: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    battery: str = "trained"
    visits: tuple[str, ...] = ()
    description: str = ""

    def formula(self, rung: Rung) -> str:
        """The lme4 formula of a rung (``KeyError`` when the rung is not in the model)."""
        return f"{self.response} ~ {' + '.join(self.fixed)} + {' + '.join(self.random[rung])}"

    @property
    def columns(self) -> tuple[str, ...]:
        """Columns of the model data CSV (after the ``data_kind`` watermark)."""
        return (self.response, *self.groups, *self.factors, *self.numeric)


def model_specs(study: str) -> tuple[ModelSpec, ...]:
    """The prespecified supporting models of a study (section 5).

    Study A: trained-message trials of D0 and D7 (``endpoint``); fixed method (reference
    A2), profile family, repetition, semantic family and endpoint; random intercepts for
    batch, book (nested in batch and method: book IDs are unique), learner (nested in
    book) and message. The designer-sensitive variant replaces method by method x A1
    designer. Study A has no random slopes, so only the ``full`` rung is fitted.

    Study B: trained-message trials of W1 and W4; fixed role (``role_c`` +0.5 active,
    -0.5 yoked), teaching format (``format_c`` +0.5 structured, -0.5 dictionary), their
    interaction, semantic family, repetition and visit; random dyad intercept with a role
    slope, participant intercept (nested in dyad) with a teaching-format slope, message
    intercept. Centred numeric codes make ``||`` remove the correlations exactly.
    """
    if study == "A":
        base = dict(
            study="A",
            response="y",
            random={"full": _A_RANDOM},
            groups=("unit_id", "book_id", "person_id", "item_id"),
            visits=("D0", "D7"),
        )
        designer_levels = ("A2", "A1-D1", "A1-D2", "A1-D3", "A3")
        return (
            ModelSpec(
                model_id="A-trained",
                fixed=("method", "profile", "rep", "family", "endpoint"),
                factors=_A_FACTORS,
                description="Trained-message trials, D0 and D7 (method contrasts).",
                **base,  # type: ignore[arg-type]
            ),
            ModelSpec(
                model_id="A-designer",
                fixed=("method_designer", "profile", "rep", "family", "endpoint"),
                factors={"method_designer": designer_levels, **_A_FACTORS},
                description="Designer-sensitive variant: A1 split by designer.",
                **base,  # type: ignore[arg-type]
            ),
        )
    if study == "B":
        return (
            ModelSpec(
                model_id="B-trained",
                study="B",
                response="y",
                fixed=("role_c * format_c", "family", "rep", "visit"),
                random=_B_RANDOM,
                factors=_B_FACTORS,
                numeric=("role_c", "format_c"),
                groups=("unit_id", "person_id", "item_id"),
                visits=("W1", "W4"),
                description="Trained-message trials, W1 and W4 (role, teaching format).",
            ),
        )
    raise ValueError(f"unknown study {study!r}")


MODEL_TIMINGS: Final[tuple[str, ...]] = ("in_window", "not_applicable")


def model_data(
    spec: ModelSpec,
    opportunities: Sequence[ScoredTrial],
    conditions: Conditions,
    *,
    data_kind: DataKind,
) -> bytes:
    """The model data CSV of ``spec`` (first column ``data_kind``, then
    :attr:`ModelSpec.columns`): every response opportunity of the model's battery and
    visits that was held in its window (or at the anchor visit), from every assigned
    person with data, complete or partial (the available-observation model). ``y`` is the
    operational score (technical failures 0)."""
    rows = []
    for o in opportunities:
        row = o.row
        if o.battery != spec.battery or row.get("visit") not in spec.visits:
            continue
        if row.get("timing") not in MODEL_TIMINGS or o.score.y_operational is None:
            continue
        person = o.person_id
        if person not in conditions.person_unit:
            raise ValueError(f"{person} is not in the {spec.study} allocation lists")
        values: dict[str, str] = {
            "y": str(o.score.y_operational),
            "unit_id": conditions.person_unit[person],
            "person_id": person,
            "item_id": str(row.get("item_id")),
            "rep": str(row.get("pass")),
            "family": str(o.family),
        }
        if spec.study == "A":
            book = conditions.books[conditions.person_book[person]]
            values.update(
                book_id=book.book_id,
                method=book.method,
                method_designer=f"A1-{book.designer}" if book.method == "A1" else book.method,
                profile=book.profile,
                endpoint=str(row.get("visit")),
            )
        else:
            dyad = conditions.dyads[conditions.person_unit[person]]
            active = conditions.person_condition[person] == "active"
            values.update(
                role_c="0.5" if active else "-0.5",
                format_c="0.5" if o.family == dyad.structured_family else "-0.5",
                visit=str(row.get("visit")),
            )
        rows.append([data_kind, *(values[c] for c in spec.columns)])
    return csv_bytes(("data_kind", *spec.columns), rows)


@dataclass(frozen=True)
class FixedEffect:
    """One fixed-effect row of an accepted fit (logit scale)."""

    term: str
    estimate: float
    se: float
    z: float
    p: float


@dataclass(frozen=True)
class RandomEffect:
    """One random-effect standard deviation (``term2`` None) or correlation."""

    group: str
    term1: str
    term2: str | None
    sdcor: float


@dataclass(frozen=True)
class GlmmFit:
    """The ladder log of a model plus the estimates of its accepted rung (if any)."""

    log: GlmmLog
    fixed: tuple[FixedEffect, ...] = ()
    random: tuple[RandomEffect, ...] = ()
    n_obs: int | None = None


def _request(spec: ModelSpec, rung: Rung) -> dict[str, Any]:
    return {
        "format": REQUEST_FORMAT,
        "model_id": spec.model_id,
        "formula": spec.formula(rung),
        "data": "data.csv",
        "response": spec.response,
        "factors": {k: list(v) for k, v in spec.factors.items()},
        "numeric": list(spec.numeric),
        "groups": list(spec.groups),
        "optimizer": OPTIMIZER,
        "max_fun": MAX_FUN,
        "singular_tol": SINGULAR_TOL,
    }


def _skip_reason(spec: ModelSpec, rung: Rung) -> str:
    if spec.study == "A":
        return "not in the Study A model: random intercepts only, no slopes or correlations"
    return f"rung {rung} is not defined for {spec.model_id}"


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return math.nan
    return float(value)


def fit_ladder(
    spec: ModelSpec,
    data_csv: bytes,
    *,
    data_kind: DataKind,
    runner: Runner | None = None,
) -> GlmmFit:
    """Fit ``spec`` rung by rung (``runner`` defaults to ``rbridge.run_r``).

    A rung is accepted when the fit converged (optimizer code 0, no lme4 convergence
    message, no convergence warning), is not singular (``isSingular``, tolerance
    :data:`SINGULAR_TOL`) and raised no error; the ladder stops there. A rung whose terms
    are not in the model is logged ``skipped``. When no rung is accepted the log ends with
    ``descriptive`` (accepted): the report shows participant/dyad aggregates instead.
    A runner exception (R missing, wrong versions) propagates: callers decide.
    """
    if runner is None:
        from .rbridge import run_r

        runner = run_r
    attempts: list[LadderAttempt] = []
    engine: Engine | None = None
    accepted: Mapping[str, Any] | None = None
    for rung in LADDER[:-1]:
        if rung not in spec.random:
            attempts.append(
                LadderAttempt(rung, None, "skipped", None, None, (), _skip_reason(spec, rung))
            )
            continue
        result = runner(RSCRIPT, _request(spec, rung), {"data.csv": data_csv})
        versions = result.get("versions", {})
        engine = Engine(
            "lme4::glmer",
            str(versions.get("R", "")),
            str(versions.get("lme4", "")),
            str(result.get("optimizer", OPTIMIZER)),
        )
        error = result.get("error")
        converged = bool(result.get("converged")) and error is None
        singular_raw = result.get("singular")
        singular = None if singular_raw is None else bool(singular_raw)
        messages = tuple(str(m) for m in result.get("messages", []))
        ok = converged and singular is False
        reasons = []
        if error is not None:
            reasons.append(f"error: {error}")
        elif not converged:
            reasons.append("did not converge")
        if singular:
            reasons.append("singular (boundary) fit")
        attempts.append(
            LadderAttempt(
                rung,
                spec.formula(rung),
                "accepted" if ok else "failed",
                converged,
                singular,
                messages,
                "; ".join(reasons),
            )
        )
        if ok:
            accepted = result
            break
    if accepted is None:
        attempts.append(
            LadderAttempt(
                "descriptive",
                None,
                "accepted",
                None,
                None,
                (),
                "no stable model: participant/dyad aggregates are reported instead",
            )
        )
    log = GlmmLog(
        data_kind, spec.study, spec.model_id, engine, sha256_bytes(data_csv), tuple(attempts)
    )
    if accepted is None:
        return GlmmFit(log)
    fixed = tuple(
        FixedEffect(
            str(f["term"]), _float(f["estimate"]), _float(f["se"]), _float(f["z"]), _float(f["p"])
        )
        for f in accepted.get("fixed", [])
    )
    random = tuple(
        RandomEffect(
            str(r["group"]),
            str(r["term1"]),
            None if r.get("term2") is None else str(r["term2"]),
            _float(r["sdcor"]),
        )
        for r in accepted.get("random", [])
    )
    n_obs = accepted.get("n_obs")
    return GlmmFit(log, fixed, random, n_obs if isinstance(n_obs, int) else None)


def fit_with_ladder(spec: ModelSpec, data_csv: bytes, *, data_kind: DataKind) -> GlmmLog:
    """Fit ``spec`` through the ladder (R via ``rbridge``) and return the full log."""
    return fit_ladder(spec, data_csv, data_kind=data_kind).log


def not_run_log(spec: ModelSpec, data_csv: bytes, *, data_kind: DataKind, reason: str) -> GlmmLog:
    """The log of a model that was not fitted (for example R missing on a SYNTHETIC run):
    every model rung ``skipped`` with ``reason``, then ``descriptive``."""
    attempts = [
        LadderAttempt(
            rung,
            spec.formula(rung) if rung in spec.random else None,
            "skipped",
            None,
            None,
            (),
            reason if rung in spec.random else _skip_reason(spec, rung),
        )
        for rung in LADDER[:-1]
    ]
    attempts.append(
        LadderAttempt(
            "descriptive", None, "accepted", None, None, (), f"model not fitted: {reason}"
        )
    )
    return GlmmLog(
        data_kind, spec.study, spec.model_id, None, sha256_bytes(data_csv), tuple(attempts)
    )
