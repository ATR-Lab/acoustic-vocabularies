"""Admissibility validator (Study A protocol §3.2; renderer spec D11).

`validate()` is the one admissibility check used by A1, A2, A3 and the Study B bank
builder. It returns one `ValidationResult` per candidate with every failing reason
code, in the fixed order of `REASON_CODES`. It never edits or repairs a recipe.

Order of work:

1. Parse (`E_JSON`) and check the recipe schema (`E_SCHEMA`, `E_DOMAIN`). If any of
   these fail, the result lists only these codes.
2. Otherwise render once and evaluate every remaining check: `E_EVENT_SHORT`,
   `E_NONFINITE`, `E_CLIP`, `E_DUPLICATE`, `E_RESERVED`, `E_SEPARATION`.

Duplicates and separation are checked against every supplied committed reference.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any

from av_sound._paths import config_path
from av_sound._schemas import StrictJsonError, load_json_file, schema_validator, strict_loads
from av_sound.features import (
    N_FEATURES,
    FeatureLike,
    Features,
    ThresholdLike,
    as_features,
    distance_from_sum_sq,
    features,
    format_fraction,
    parse_threshold,
    sum_squared_diff,
)
from av_sound.recipe import E_DOMAIN, E_SCHEMA, Profile, Recipe, RecipeError
from av_sound.renderer import MIN_EVENT_SAMPLES, RENDERER_VERSION, Rendered, render
from av_sound.reserved import ReservedEntry, ReservedRegistry, load_reserved_registry
from av_sound.tables import SAMPLES_PER_MS

VALIDATOR_VERSION = "0.1.0"
"""Bumped whenever a validator decision can change for some input (pilot until G4)."""

RESULT_VERSION = 1
"""Version of the `ValidationResult.to_dict()` format (`validation-result.schema.json`)."""

E_JSON = "E_JSON"
E_EVENT_SHORT = "E_EVENT_SHORT"
E_NONFINITE = "E_NONFINITE"
E_CLIP = "E_CLIP"
E_DUPLICATE = "E_DUPLICATE"
E_RESERVED = "E_RESERVED"
E_SEPARATION = "E_SEPARATION"

REASON_CODES: tuple[str, ...] = (
    E_JSON,
    E_SCHEMA,
    E_DOMAIN,
    E_EVENT_SHORT,
    E_NONFINITE,
    E_CLIP,
    E_DUPLICATE,
    E_RESERVED,
    E_SEPARATION,
)
"""Every reason code, in the fixed order used by `ValidationResult.codes`."""

_INTEGER_FIELDS: tuple[str, ...] = ("total_ms", "pitches", "rhythm_weights", "gaps_ms")
_MESSAGE_LIMIT = 400


def _clip_text(text: str) -> str:
    return text if len(text) <= _MESSAGE_LIMIT else text[: _MESSAGE_LIMIT - 3] + "..."


# ---------------------------------------------------------------------------
# References and nearest reference


@dataclass(frozen=True, slots=True)
class Reference:
    """A committed motif (or retained bank option) that candidates are checked against.

    `ref_id` is the caller's identifier (atom ID, option ID). Its position in the
    `committed` sequence is its index for the nearest-reference tie rule.
    """

    ref_id: str
    recipe: Recipe
    pcm_sha256: str
    profile: Profile
    features: Features = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.ref_id, str) or not self.ref_id:
            raise ValueError("Reference.ref_id must be a non-empty string")
        if not isinstance(self.recipe, Recipe):
            raise TypeError("Reference.recipe must be a Recipe")
        digest = self.pcm_sha256
        if not (
            isinstance(digest, str)
            and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Reference.pcm_sha256 must be lowercase hex SHA-256")
        object.__setattr__(self, "profile", Profile(self.profile))
        object.__setattr__(self, "features", features(self.recipe))

    @classmethod
    def from_rendered(cls, ref_id: str, rendered: Rendered) -> Reference:
        """Reference for a rendered motif. Raises `OverflowError` if it overflowed."""
        return cls(ref_id, rendered.recipe, rendered.pcm_sha256, rendered.profile)


@dataclass(frozen=True, slots=True)
class NearestReference:
    """The committed reference closest to a candidate (ties: lowest index)."""

    ref_id: str
    index: int
    distance: float
    sum_sq: Fraction
    """Exact `sum((x_j - y_j)^2)`; `distance == sqrt(sum_sq / 12)`."""


def _references(committed: Iterable[Reference], profile: Profile | None) -> tuple[Reference, ...]:
    refs = tuple(committed)
    for i, ref in enumerate(refs):
        if not isinstance(ref, Reference):
            raise TypeError(f"committed[{i}] is {type(ref).__name__}, expected Reference")
    expected = profile if profile is not None else (refs[0].profile if refs else None)
    mismatched = [r.ref_id for r in refs if r.profile != expected]
    if mismatched:
        raise ValueError(
            f"committed references must all use profile {expected}; got others for {mismatched}"
        )
    return refs


def _nearest(sums: Sequence[Fraction], refs: Sequence[Reference]) -> NearestReference | None:
    best: int | None = None
    for i, s in enumerate(sums):
        if best is None or s < sums[best]:
            best = i
    if best is None:
        return None
    return NearestReference(
        ref_id=refs[best].ref_id,
        index=best,
        distance=distance_from_sum_sq(sums[best]),
        sum_sq=sums[best],
    )


def nearest_reference(
    candidate: FeatureLike,
    committed: Iterable[Reference],
    *,
    profile: Profile | str | None = None,
) -> NearestReference | None:
    """Nearest committed reference by 12-feature distance; ties go to the lowest index.

    Returns `None` when nothing is committed (the first atom). All references must
    share one profile (`profile`, when given); otherwise `ValueError`.
    """
    refs = _references(committed, None if profile is None else Profile(profile))
    cand = as_features(candidate)
    return _nearest([sum_squared_diff(cand, r.features) for r in refs], refs)


# ---------------------------------------------------------------------------
# Threshold configuration


@lru_cache(maxsize=8)
def _threshold_cached(path: str, _mtime_ns: int, _size: int) -> Fraction:
    data = load_json_file(Path(path))
    errors = sorted(
        e.message for e in schema_validator("validator-config.schema.json").iter_errors(data)
    )
    if errors:
        raise ValueError(f"{path}: validator config does not match its schema: {'; '.join(errors)}")
    assert isinstance(data, dict)
    return parse_threshold(data["separation_threshold"])


def load_separation_threshold(path: str | os.PathLike[str] | None = None) -> Fraction:
    """The configured separation threshold (default file `sound/config/validator.json`).

    Stored as a decimal string and returned as an exact `Fraction` (`"0.10"` -> 1/10).
    """
    target = Path(path) if path is not None else config_path("validator.json")
    stat = target.stat()
    return _threshold_cached(str(target.resolve()), stat.st_mtime_ns, stat.st_size)


# ---------------------------------------------------------------------------
# Result


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """One validator decision. `ok` is true exactly when `codes` is empty.

    `messages[i]` explains `codes[i]`. `features`, `event_samples` and the nearest
    reference are present whenever the recipe parsed. `pcm_sha256` is `None` when the
    recipe did not parse or the waveform is unusable (`E_NONFINITE`, `E_CLIP`).
    """

    ok: bool
    codes: tuple[str, ...]
    messages: tuple[str, ...]
    profile: Profile
    threshold: Fraction
    recipe: Recipe | None = None
    features: Features | None = None
    event_samples: tuple[int, int, int] | None = None
    pcm_sha256: str | None = None
    nearest_id: str | None = None
    nearest_index: int | None = None
    nearest_distance: float | None = None
    validator_version: str = VALIDATOR_VERSION
    renderer_version: str = RENDERER_VERSION
    rendered: Rendered | None = field(default=None, repr=False, compare=False)
    """The render used for the checks (not serialized), so callers need not render again."""

    @property
    def primary_code(self) -> str | None:
        """The first failing code in `REASON_CODES` order, or `None` when `ok`."""
        return self.codes[0] if self.codes else None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict matching `sound/schema/validation-result.schema.json`."""
        return {
            "result_version": RESULT_VERSION,
            "ok": self.ok,
            "codes": list(self.codes),
            "messages": list(self.messages),
            "profile": self.profile.value,
            "threshold": format_fraction(self.threshold),
            "recipe": None if self.recipe is None else self.recipe.to_dict(),
            "recipe_sha256": None if self.recipe is None else self.recipe.sha256(),
            "features": None if self.features is None else [str(f) for f in self.features],
            "event_samples": None if self.event_samples is None else list(self.event_samples),
            "pcm_sha256": self.pcm_sha256,
            "nearest_id": self.nearest_id,
            "nearest_index": self.nearest_index,
            "nearest_distance": self.nearest_distance,
            "validator_version": self.validator_version,
            "renderer_version": self.renderer_version,
        }


def _ordered(failures: Mapping[str, list[str]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Failing codes in `REASON_CODES` order and one message per code."""
    codes = tuple(c for c in REASON_CODES if failures.get(c))
    return codes, tuple(_clip_text("; ".join(failures[c])) for c in codes)


# ---------------------------------------------------------------------------
# Parsing and schema/domain checks


def _plain(candidate: object) -> object:
    """Mappings become dicts and tuple/list values become lists (one level, as in JSON)."""
    if isinstance(candidate, Mapping):
        return {k: list(v) if isinstance(v, list | tuple) else v for k, v in candidate.items()}
    return candidate


def _path_text(path: Sequence[object]) -> str:
    return "/".join(str(p) for p in path) or "<root>"


def _schema_failures(data: object) -> dict[str, list[str]]:
    """Schema errors as E_SCHEMA; `enum` errors as E_DOMAIN unless the value's type is wrong."""
    type_paths: set[tuple[object, ...]] = set()
    schema: list[tuple[str, str]] = []
    domain: list[tuple[tuple[object, ...], str]] = []
    for err in schema_validator("recipe.schema.json").iter_errors(data):
        path = tuple(err.absolute_path)
        if err.validator == "enum":
            domain.append((path, err.message))
        else:
            type_paths.add(path)
            schema.append((_path_text(path), err.message))
    # Integer fields given as numbers with a fraction part (450.0) pass JSON Schema's
    # "integer" type; renderer spec D11 rejects them, like RecipeError does (E_SCHEMA).
    if isinstance(data, dict):
        for name in _INTEGER_FIELDS:
            value = data.get(name)
            items = (
                [((name,), value)]
                if not isinstance(value, list)
                else [((name, i), v) for i, v in enumerate(value)]
            )
            for path, v in items:
                if isinstance(v, float) and v.is_integer():
                    type_paths.add(path)
                    schema.append((_path_text(path), f"{v!r} is a number, expected an integer"))
    failures: dict[str, list[str]] = {}
    if schema:
        failures[E_SCHEMA] = [_clip_text(f"{p}: {m}") for p, m in sorted(schema)]
    domain_messages = sorted(
        (_path_text(path), message) for path, message in domain if path not in type_paths
    )
    if domain_messages:
        failures[E_DOMAIN] = [_clip_text(f"{p}: {m}") for p, m in domain_messages]
    return failures


def _parse(candidate: object) -> tuple[Recipe | None, dict[str, list[str]]]:
    if isinstance(candidate, Recipe):
        return candidate, {}
    if isinstance(candidate, str | bytes | bytearray):
        try:
            data: object = strict_loads(candidate)
        except StrictJsonError as exc:
            return None, {E_JSON: [_clip_text(f"invalid JSON: {exc}")]}
    else:
        data = _plain(candidate)
    failures = _schema_failures(data)
    if failures:
        return None, failures
    assert isinstance(data, dict)
    try:
        return Recipe.from_dict(data), {}
    except RecipeError as exc:  # pragma: no cover - schema and Recipe agree (tested)
        return None, {exc.code: [str(exc)]}


# ---------------------------------------------------------------------------
# validate()


def _reserved_entries(
    reserved: ReservedRegistry | Iterable[ReservedEntry] | None,
) -> tuple[ReservedEntry, ...]:
    if reserved is None:
        reserved = load_reserved_registry()
    if isinstance(reserved, ReservedRegistry):
        if reserved.renderer_version != RENDERER_VERSION:
            raise ValueError(
                f"reserved registry was built with renderer {reserved.renderer_version}, "
                f"current renderer is {RENDERER_VERSION}; rebuild the registry (#14)"
            )
        return reserved.entries
    entries = tuple(reserved)
    for i, entry in enumerate(entries):
        if not isinstance(entry, ReservedEntry):
            raise TypeError(f"reserved[{i}] is {type(entry).__name__}, expected ReservedEntry")
    return entries


def _ms(samples: int) -> str:
    return f"{samples / SAMPLES_PER_MS:.1f} ms"


def validate(
    candidate: Recipe | Mapping[str, Any] | str | bytes | bytearray,
    profile: Profile | str,
    committed: Iterable[Reference] = (),
    *,
    reserved: ReservedRegistry | Iterable[ReservedEntry] | None = None,
    threshold: ThresholdLike | None = None,
) -> ValidationResult:
    """Check one candidate recipe for `profile` against the committed references.

    - `candidate`: raw JSON text or bytes (UTF-8), a decoded JSON object, or a `Recipe`.
      Any candidate value gives a result; bad candidates never raise.
    - `committed`: the ordered references of the same book and profile (both families
      and roles; commit order is the index). A reference with another profile raises
      `ValueError` (caller error).
    - `reserved`: a registry or its entries; `None` loads `sound/reserved/registry.json`.
    - `threshold`: exact separation threshold; `None` loads `sound/config/validator.json`.

    Distance at or above the threshold passes; below fails (decided exactly).
    """
    prof = Profile(profile)
    sep_threshold = load_separation_threshold() if threshold is None else parse_threshold(threshold)
    limit = N_FEATURES * sep_threshold * sep_threshold
    refs = _references(committed, prof)
    entries = _reserved_entries(reserved)

    recipe, failures = _parse(candidate)
    if recipe is None:
        codes, messages = _ordered(failures)
        return ValidationResult(
            ok=False, codes=codes, messages=messages, profile=prof, threshold=sep_threshold
        )

    rendered = render(recipe, prof)
    feats = features(recipe)
    n = rendered.event_samples

    if rendered.short_event:
        short = [
            f"event {j + 1} is {s} samples ({_ms(s)})"
            for j, s in enumerate(n)
            if s < MIN_EVENT_SAMPLES
        ]
        failures[E_EVENT_SHORT] = [
            f"{', '.join(short)}; minimum {MIN_EVENT_SAMPLES} samples ({_ms(MIN_EVENT_SAMPLES)})"
        ]
    if rendered.nonfinite:
        failures[E_NONFINITE] = ["the rendered waveform contains non-finite samples"]
    if rendered.overflow:
        failures[E_CLIP] = [
            f"a sample exceeds full scale after normalization (peak {rendered.peak})"
        ]
    usable = not (rendered.nonfinite or rendered.overflow)
    pcm = rendered.pcm_sha256 if usable else None

    if pcm is not None:
        same = [r.ref_id for r in refs if r.pcm_sha256 == pcm]
        if same:
            failures[E_DUPLICATE] = [f"waveform identical to committed {', '.join(same)}"]

    hits: list[str] = []
    for entry in entries:
        if pcm is not None and entry.pcm_sha256 == pcm:
            hits.append(f"waveform identical to reserved {entry.id} ({entry.kind})")
        elif (
            entry.features is not None
            and (entry.profile is None or entry.profile == prof)
            and sum_squared_diff(feats, entry.features) < limit
        ):
            hits.append(f"closer than the threshold to reserved {entry.id} ({entry.kind})")
    if hits:
        failures[E_RESERVED] = hits

    sums = [sum_squared_diff(feats, r.features) for r in refs]
    close = [
        f"{r.ref_id} at {distance_from_sum_sq(s):.6f}"
        for r, s in zip(refs, sums, strict=True)
        if s < limit
    ]
    if close:
        failures[E_SEPARATION] = [
            f"distance below {format_fraction(sep_threshold)} to {', '.join(close)}"
        ]
    nearest = _nearest(sums, refs)

    codes, messages = _ordered(failures)
    return ValidationResult(
        ok=not codes,
        codes=codes,
        messages=messages,
        profile=prof,
        threshold=sep_threshold,
        recipe=recipe,
        features=feats,
        event_samples=n,
        pcm_sha256=pcm,
        nearest_id=None if nearest is None else nearest.ref_id,
        nearest_index=None if nearest is None else nearest.index,
        nearest_distance=None if nearest is None else nearest.distance,
        rendered=rendered,
    )
