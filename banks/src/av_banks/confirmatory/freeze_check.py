"""The G4 freeze manifest of a campaign: #25's freeze guard and the freeze tag (#28).

- `load_freeze(path, *, kind)` reads the manifest through #25's
  `freeze.load_freeze_manifest`. A confirmatory campaign calls it with
  `require_frozen=True`, which checks the manifest, refuses a draft and runs the CI freeze
  guard against the running code and the committed files, so a changed checkout never
  plans or runs a confirmatory campaign (#25 contract, `docs/interfaces/generation.md`).
  A DEMO campaign calls it without `require_frozen` (manifest checks only). A checkout
  without #25's guard (`guard_available()` false: the skeleton `freeze` module defines
  the format only) checks a DEMO manifest against `freeze-manifest.schema.json` and
  refuses a confirmatory campaign.
- `check_tag(repo, manifest, data)` checks the freeze tag in a git repository: the tag
  resolves to a commit, the manifest file committed there (`generation/FREEZE-v<freeze
  version>.json` unless `manifest_path` says otherwise) is byte-identical to the given
  manifest, and `repo_commit` is that commit or an ancestor of it. In #25's procedure
  (`generation/docs/freeze.md` sections 2 and 6) `repo_commit` is the commit the values
  were collected from, and the tag marks the later commit that adds the manifest, so the
  two differ; a commit can never contain its own hash.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from av_generation import freeze as _freeze
from av_generation._schemas import schema_errors as generation_schema_errors
from av_generation.ids import RunKind
from av_generation.jsonio import read_json

from .common import E_FREEZE, CampaignError

FREEZE_SCHEMA: Final = "freeze-manifest.schema.json"
MANIFEST_DIR: Final = "generation"
"""Where #25 commits the frozen manifest (`generation/FREEZE-v1.0.json`)."""
REPO_PATH_RE: Final = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*")
MAX_PROBLEMS: Final = 5


@dataclass(frozen=True, slots=True)
class LoadedFreeze:
    """A checked freeze manifest, its file bytes and their SHA-256."""

    manifest: dict[str, Any]
    data: bytes
    sha256: str
    guard_checked: bool
    """Whether #25's freeze guard compared the running code and committed files with it."""


def guard_available() -> bool:
    """Whether this checkout has #25's `freeze.load_freeze_manifest`."""
    return callable(getattr(_freeze, "load_freeze_manifest", None))


def _guard_error(err: Exception) -> str:
    lines = str(err).splitlines() or [type(err).__name__]
    problems = tuple(getattr(err, "problems", ()) or ())
    text = lines[0]
    if problems:
        shown = "; ".join(str(p) for p in problems[:MAX_PROBLEMS])
        more = " ..." if len(problems) > MAX_PROBLEMS else ""
        text += f" ({len(problems)}: {shown}{more})"
    return text


def load_freeze(path: str | os.PathLike[str], *, kind: RunKind | str) -> LoadedFreeze:
    """Read and check a freeze manifest for a campaign of `kind` (module docstring).
    Raises `CampaignError(E_FREEZE)`."""
    kind = RunKind(kind)
    confirmatory = kind is RunKind.CONFIRMATORY
    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as err:
        raise CampaignError(E_FREEZE, f"cannot read the freeze manifest: {err}") from err
    digest = hashlib.sha256(data).hexdigest()
    loader = getattr(_freeze, "load_freeze_manifest", None)
    if not callable(loader):
        if confirmatory:
            raise CampaignError(
                E_FREEZE,
                "this checkout has no G4 freeze guard (#25 freeze.load_freeze_manifest); "
                "a confirmatory campaign cannot be planned or run without it",
            )
        try:
            doc = read_json(source)
        except (OSError, ValueError) as err:
            raise CampaignError(E_FREEZE, f"cannot read the freeze manifest: {err}") from err
        if not isinstance(doc, dict):
            raise CampaignError(E_FREEZE, "the freeze manifest is not a JSON object")
        errors = generation_schema_errors(FREEZE_SCHEMA, doc)
        if errors:
            raise CampaignError(
                E_FREEZE, f"freeze manifest does not match {FREEZE_SCHEMA}: {list(errors[:3])}"
            )
        return LoadedFreeze(doc, data, digest, False)
    try:
        loaded = loader(source, require_frozen=confirmatory)
    except (OSError, ValueError) as err:  # #25 FreezeError is a ValueError
        raise CampaignError(
            E_FREEZE, f"refused by the G4 freeze guard: {_guard_error(err)}"
        ) from err
    manifest = getattr(loaded, "manifest", None)
    if not isinstance(manifest, dict) or getattr(loaded, "sha256", None) != digest:
        raise CampaignError(E_FREEZE, "the freeze manifest changed while it was read")
    return LoadedFreeze(manifest, data, digest, confirmatory)


def default_manifest_path(manifest: Mapping[str, Any]) -> str:
    """`generation/FREEZE-v<freeze_version>.json`, where #25 commits a frozen manifest."""
    return f"{MANIFEST_DIR}/FREEZE-v{manifest.get('freeze_version')}.json"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)  # noqa: S603


def check_tag(
    repo: str | os.PathLike[str],
    manifest: Mapping[str, Any],
    data: bytes,
    *,
    manifest_path: str | None = None,
) -> str:
    """Check the freeze tag of `manifest` (file bytes `data`) in the git repository
    `repo` (module docstring); returns the commit the tag points at. Raises
    `CampaignError(E_FREEZE)`."""
    base = Path(repo)
    tag = manifest.get("tag")
    commit = manifest.get("repo_commit")
    if not isinstance(tag, str):
        raise CampaignError(E_FREEZE, "the freeze manifest names no tag to check")
    if not isinstance(commit, str):
        raise CampaignError(E_FREEZE, "the freeze manifest names no repo_commit to check")
    rel = manifest_path if manifest_path is not None else default_manifest_path(manifest)
    rel = rel.replace("\\", "/")
    if not REPO_PATH_RE.fullmatch(rel) or ".." in rel.split("/"):
        raise CampaignError(E_FREEZE, f"{rel!r} is not a repository-relative POSIX path")
    resolved = _git(base, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    tagged = resolved.stdout.decode("ascii", errors="replace").strip()
    if resolved.returncode != 0 or not tagged:
        raise CampaignError(E_FREEZE, f"tag {tag!r} does not exist in {base.name or base}")
    shown = _git(base, "show", f"{tagged}:{rel}")
    if shown.returncode != 0:
        raise CampaignError(E_FREEZE, f"the commit of tag {tag!r} ({tagged}) has no {rel}")
    if shown.stdout != data:
        raise CampaignError(
            E_FREEZE,
            f"{rel} at tag {tag!r} is not this freeze manifest (the bytes differ: use the "
            "committed file)",
        )
    ancestry = _git(base, "merge-base", "--is-ancestor", commit, tagged)
    if ancestry.returncode == 1:
        raise CampaignError(
            E_FREEZE,
            f"repo_commit {commit} is neither the commit of tag {tag!r} ({tagged}) nor an "
            "ancestor of it",
        )
    if ancestry.returncode != 0:
        raise CampaignError(E_FREEZE, f"repo_commit {commit} is not a commit of the repository")
    return tagged
