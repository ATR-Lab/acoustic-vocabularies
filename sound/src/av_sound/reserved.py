"""Reserved-signal registry (`sound/reserved/registry.json`).

Reserved signals are nonlexical assets that must never be admissible as motifs: the
calibration examples, the READY cue and the grammar clicks (#14 fills the
registry). The validator reports `E_RESERVED` when a candidate's waveform hash
equals an entry's, or when an entry has a recipe and the candidate is closer to it
than the separation threshold (same profile, or an entry with no profile).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from av_sound._paths import reserved_path
from av_sound._schemas import load_json_file, schema_validator
from av_sound.features import Features, features
from av_sound.recipe import Profile, Recipe

REGISTRY_VERSION = 1
RESERVED_KINDS: tuple[str, ...] = ("calibration", "ready_cue", "click", "other")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ReservedEntry:
    """One reserved signal. `profile=None` means the entry applies to every profile."""

    id: str
    kind: str
    profile: Profile | None
    n_samples: int
    pcm_sha256: str
    file_sha256: str
    recipe: Recipe | None
    description: str
    features: Features | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("reserved entry: id must be a non-empty string")
        if self.kind not in RESERVED_KINDS:
            raise ValueError(f"reserved entry {self.id}: kind must be one of {RESERVED_KINDS}")
        if self.profile is not None:
            object.__setattr__(self, "profile", Profile(self.profile))
        if isinstance(self.n_samples, bool) or not isinstance(self.n_samples, int):
            raise ValueError(f"reserved entry {self.id}: n_samples must be an integer")
        if self.n_samples < 1:
            raise ValueError(f"reserved entry {self.id}: n_samples must be positive")
        for name in ("pcm_sha256", "file_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"reserved entry {self.id}: {name} must be lowercase hex SHA-256")
        if self.recipe is not None and not isinstance(self.recipe, Recipe):
            raise TypeError(f"reserved entry {self.id}: recipe must be a Recipe or None")
        object.__setattr__(self, "features", None if self.recipe is None else features(self.recipe))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReservedEntry:
        """Build an entry from its registry JSON object."""
        recipe = data["recipe"]
        return cls(
            id=data["id"],
            kind=data["kind"],
            profile=None if data["profile"] is None else Profile(data["profile"]),
            n_samples=data["n_samples"],
            pcm_sha256=data["pcm_sha256"],
            file_sha256=data["file_sha256"],
            recipe=None if recipe is None else Recipe.from_dict(recipe),
            description=data["description"],
        )

    def to_dict(self) -> dict[str, Any]:
        """The registry JSON object for this entry."""
        return {
            "id": self.id,
            "kind": self.kind,
            "profile": None if self.profile is None else self.profile.value,
            "n_samples": self.n_samples,
            "pcm_sha256": self.pcm_sha256,
            "file_sha256": self.file_sha256,
            "recipe": None if self.recipe is None else self.recipe.to_dict(),
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class ReservedRegistry:
    """The decoded registry file. Entry IDs are unique."""

    registry_version: int
    renderer_version: str
    entries: tuple[ReservedEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        ids = [e.id for e in self.entries]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"reserved registry: duplicate entry ids {duplicates}")

    @classmethod
    def from_dict(cls, data: object) -> ReservedRegistry:
        """Check `data` against `reserved-registry.schema.json` and build the registry."""
        errors = sorted(
            schema_validator("reserved-registry.schema.json").iter_errors(data),
            key=lambda e: (list(map(str, e.absolute_path)), e.message),
        )
        if errors:
            details = "; ".join(
                f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors
            )
            raise ValueError(f"reserved registry does not match its schema: {details}")
        assert isinstance(data, dict)
        return cls(
            registry_version=data["registry_version"],
            renderer_version=data["renderer_version"],
            entries=tuple(ReservedEntry.from_dict(e) for e in data["entries"]),
        )

    def to_dict(self) -> dict[str, Any]:
        """The registry JSON object."""
        return {
            "registry_version": self.registry_version,
            "renderer_version": self.renderer_version,
            "entries": [e.to_dict() for e in self.entries],
        }


@lru_cache(maxsize=8)
def _load_cached(path: str, _mtime_ns: int, _size: int) -> ReservedRegistry:
    return ReservedRegistry.from_dict(load_json_file(Path(path)))


def load_reserved_registry(path: str | os.PathLike[str] | None = None) -> ReservedRegistry:
    """Load and check a registry file (default `sound/reserved/registry.json`).

    The decoded registry is cached until the file's size or modification time changes.
    Raises `ValueError` if the file is not strict JSON or does not match the schema.
    """
    target = Path(path) if path is not None else reserved_path()
    stat = target.stat()
    return _load_cached(str(target.resolve()), stat.st_mtime_ns, stat.st_size)
