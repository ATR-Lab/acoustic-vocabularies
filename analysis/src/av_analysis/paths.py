"""Data roots: the directory layout and the SYNTHETIC/REAL watermark rule.

Every analysis command works on one **data root**, a directory with a marker file
``av-data-root.json`` that fixes its data kind (``SYNTHETIC`` or ``REAL``). Layout::

    <root>/
      av-data-root.json          marker (schema data-root.schema.json)
      raw/                       READ-ONLY: raw logs as exported at the end of each visit
        <visit_id>/              <person_id>-<visit>, e.g. A-C07-L03-D0
          trial-log.csv          methodology templates (templates.TEMPLATES[...].raw_name)
          exposure-ledger.csv
          visit-run-sheet.csv
          deviations.csv         deviations recorded during the session
          exit-manifest.json     SHA-256 of each file, saved at exit (exit-manifest.schema.json)
        deviations-log.csv       study-wide append-only log of later deviations and corrections
      inputs/                    READ-ONLY: frozen reference inputs (schedules, run sheets,
                                 package JSON, package-hash mappings, allocation lists without
                                 the method key, reveal log, store snapshots, golden manifest)
      keys/                      READ-ONLY: unmasking material (Study A book key); read by
                                 the analysis pipeline (#34) only, never by #33 or #35
      reconciled/                #33: <visit_id>/reconciliation.json and reconciled tables
      derived/                   #33: derived trial and endpoint tables
      estimates/                 #34: section 9 report, estimate tables, GLMM logs
      monitoring/                #35: integrity dashboard (static HTML)

Watermark rule (:func:`write_output`): outputs are written only into the four output
areas of a root whose marker matches the writer's data kind; JSON outputs carry
``"data_kind"`` at top level, CSV outputs a first column ``data_kind`` on every row, HTML
and Markdown outputs the marker ``av-data-kind: <KIND>`` (HTML in a ``<meta>``
element) and, for synthetic data, a visible ``SYNTHETIC`` banner. A REAL root may not lie
inside a committable path of a git work tree, and roots never nest inside a root of the
other kind. So synthetic outputs cannot land in a real-data directory, and real outputs
cannot land in a synthetic one or in the public repository.

Read-only areas (``raw/``, ``inputs/``, ``keys/``) are filled by copying exports and
frozen inputs into a root; no analysis command writes them. The one exception is
:func:`write_synthetic_input`, the only sanctioned writer of those areas, used by the
synthetic generators (#33 ``synth-logs`` and fault injection, #34 ``simulate``): it
accepts SYNTHETIC roots only and refuses content marked as real
(:func:`remove_synthetic_input` deletes a file under the same rules, for fault injection).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from .fileio import CsvFormatError, json_bytes, parse_csv, read_bytes, write_bytes
from .vocab import DATA_KINDS, DataKind

MARKER: Final = "av-data-root.json"
MARKER_FORMAT: Final = "av-analysis/data-root"
MARKER_FORMAT_VERSION: Final = 1

Area = Literal["raw", "inputs", "keys", "reconciled", "derived", "estimates", "monitoring"]
AREAS: Final[tuple[Area, ...]] = (
    "raw",
    "inputs",
    "keys",
    "reconciled",
    "derived",
    "estimates",
    "monitoring",
)
READ_ONLY_AREAS: Final[tuple[Area, ...]] = ("raw", "inputs", "keys")
OUTPUT_AREAS: Final[tuple[Area, ...]] = ("reconciled", "derived", "estimates", "monitoring")
AREA_OWNER: Final[dict[Area, str]] = {
    "reconciled": "#33",
    "derived": "#33",
    "estimates": "#34",
    "monitoring": "#35",
}

# File names inside raw/<visit_id>/ (the four CSVs are templates.Template.raw_name).
EXIT_MANIFEST: Final = "exit-manifest.json"
DEVIATIONS_LOG: Final = "deviations-log.csv"
RECONCILIATION_REPORT: Final = "reconciliation.json"
OUTPUTS_MANIFEST: Final = "manifest.json"  # <area>/manifest.json of every output area

# Reference inputs, relative to the data root (``str.format`` fields: study, set, unit_id,
# person_id, visit, package_id). ``inputs/schedules/`` is a copy of the schedules output
# folder of the set (av-schedules ``--out`` layout) WITHOUT the Study A book key, which
# goes to ``keys/``; #33 refuses an ``inputs/`` tree that contains a book key. Package
# folders hold the package JSON documents only (never WAVs). Study B store snapshots are
# the menu-store bridge's ``verified_snapshot`` result after the visit's selections
# (``docs/interfaces/menu-store-bridge.md``, #70) and ``receipts.jsonl`` its selection
# receipts in order (``before_head``/``after_head`` chain); see ``references``.
INPUT_PATHS: Final[dict[str, str]] = {
    "schedule": "inputs/schedules/{study}/{unit_id}/schedules/{person_id}/{visit}.json",
    "run_sheet": "inputs/schedules/{study}/{unit_id}/run-sheets/{person_id}/{visit}.csv",
    "slots": "inputs/schedules/A/{set}-slots.json",
    "dyads": "inputs/schedules/B/{set}-dyads.json",
    "reveal_log": "inputs/reveal/{study}-{set}.jsonl",
    "package_manifest": "inputs/packages/{package_id}/manifest.json",
    "package_audio": "inputs/packages/{package_id}/audio.json",
    "package_hashes": "inputs/schedules/{study}/{set}-package-hashes.json",
    "store_snapshot": "inputs/store-snapshots/{unit_id}/{visit}.json",
    "store_receipts": "inputs/store-snapshots/{unit_id}/receipts.jsonl",
    "golden_manifest": "inputs/sound/golden-manifest.json",
    "generation_audit": "inputs/generation/{study}-{set}-audit.csv",
    "book_key": "keys/A/{set}-book-key.json",
}

_VISIT_ID_RE: Final = re.compile(
    r"(?P<a>A-[PC][0-9]{2}-L[0-9]{2})-(?P<av>D[07])"
    r"|(?P<b>B-[PCS][0-9]{2}-M[12])-(?P<bv>V[1-3]|W[14])"
)
_LABEL_RE: Final = re.compile(r"[A-Za-z0-9._:-]{1,80}")
WATERMARK_BANNER: Final = "SYNTHETIC"
_GIT: Final = "git"


class WatermarkError(RuntimeError):
    """A write that would mix synthetic and real data, or leave a root's output areas."""


def visit_id(person_id: str, visit: str) -> str:
    """``<person_id>-<visit>`` (validated)."""
    value = f"{person_id}-{visit}"
    parse_visit_id(value)
    return value


def parse_visit_id(value: str) -> tuple[str, str]:
    """``(person_id, visit)`` of a visit ID; raises ``ValueError`` if malformed."""
    m = _VISIT_ID_RE.fullmatch(value)
    if m is None:
        raise ValueError(f"invalid visit ID {value!r} (expected e.g. A-C07-L03-D0, B-C12-M1-W1)")
    return (m["a"], m["av"]) if m["a"] else (m["b"], m["bv"])


def committable(path: Path) -> bool:
    """True if ``path`` could be committed: inside a git work tree and not git-ignored.

    Fails closed: if a ``.git`` entry exists in ``path`` or a parent but git cannot be run
    or cannot answer, the path counts as committable (same rule as the schedules CLI).
    """
    target = path.resolve()
    start = target if target.is_dir() else target.parent
    while not start.exists():
        start = start.parent
    in_repo = any((p / ".git").exists() for p in (start, *start.parents))
    try:
        top = subprocess.run(
            [_GIT, "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return in_repo
    if top.returncode != 0:
        return in_repo
    root = Path(top.stdout.strip()).resolve()
    probe = target / "probe"
    try:
        rel = probe.relative_to(root).as_posix()
    except ValueError:
        return True
    try:
        ignored = subprocess.run(
            [_GIT, "-C", str(root), "check-ignore", "-q", "--", rel],
            capture_output=True,
            check=False,
        )
    except OSError:
        return True
    return ignored.returncode != 0


def _marker_kind(directory: Path) -> DataKind | None:
    path = directory / MARKER
    if not path.is_file():
        return None
    try:
        doc = json.loads(read_bytes(path))
    except (OSError, ValueError):
        raise WatermarkError(f"unreadable data-root marker {path}") from None
    kind = doc.get("data_kind") if isinstance(doc, dict) else None
    if kind not in DATA_KINDS:
        raise WatermarkError(f"invalid data_kind in {path}")
    return kind  # type: ignore[no-any-return]


@dataclass(frozen=True)
class DataRoot:
    """An opened data root."""

    path: Path
    data_kind: DataKind
    study: str  # "A", "B" or "both"
    set_name: str  # "pilot", "confirmatory" or "both"
    label: str

    @property
    def synthetic(self) -> bool:
        return self.data_kind == "SYNTHETIC"

    @classmethod
    def create(
        cls,
        path: Path,
        data_kind: DataKind,
        *,
        study: str = "both",
        set_name: str = "both",
        label: str,
    ) -> DataRoot:
        """Create (or re-open with the same marker) a data root at ``path``."""
        if data_kind not in DATA_KINDS:
            raise ValueError(f"data_kind must be one of {DATA_KINDS}")
        if study not in ("A", "B", "both") or set_name not in ("pilot", "confirmatory", "both"):
            raise ValueError("study must be A, B or both; set must be pilot, confirmatory or both")
        if not _LABEL_RE.fullmatch(label):
            raise ValueError("label must match [A-Za-z0-9._:-]{1,80}")
        if data_kind == "SYNTHETIC" and not label.startswith("DEMO-"):
            raise ValueError("synthetic roots need a DEMO- label")
        if data_kind == "REAL" and label.startswith("DEMO-"):
            raise ValueError("a REAL root may not have a DEMO- label")
        path = path.resolve()
        _check_nesting(path, data_kind)
        if data_kind == "REAL" and committable(path):
            raise WatermarkError(f"refusing a REAL data root inside a git work tree: {path}")
        doc = {
            "format": MARKER_FORMAT,
            "format_version": MARKER_FORMAT_VERSION,
            "data_kind": data_kind,
            "study": study,
            "set": set_name,
            "label": label,
        }
        marker = path / MARKER
        if marker.exists():
            existing = json.loads(read_bytes(marker))
            if existing != doc:
                raise WatermarkError(f"{marker} exists with other settings")
        else:
            write_bytes(marker, json_bytes(doc))
        return cls.open(path)

    @classmethod
    def open(cls, path: Path) -> DataRoot:
        """Open an existing data root (its marker must be valid)."""
        path = path.resolve()
        marker = path / MARKER
        if not marker.is_file():
            raise WatermarkError(f"not a data root (no {MARKER}): {path}")
        doc: Any = json.loads(read_bytes(marker))
        expected = {"format", "format_version", "data_kind", "study", "set", "label"}
        if (
            not isinstance(doc, dict)
            or set(doc) != expected
            or doc["format"] != MARKER_FORMAT
            or doc["format_version"] != MARKER_FORMAT_VERSION
            or doc["data_kind"] not in DATA_KINDS
        ):
            raise WatermarkError(f"invalid data-root marker {marker}")
        _check_nesting(path, doc["data_kind"])
        if doc["data_kind"] == "REAL" and committable(path):
            raise WatermarkError(f"refusing a REAL data root inside a git work tree: {path}")
        return cls(path, doc["data_kind"], doc["study"], doc["set"], doc["label"])

    def area(self, area: Area) -> Path:
        """Directory of an area (not created)."""
        if area not in AREAS:
            raise ValueError(f"unknown area {area!r}")
        return self.path / area

    def raw_visit_dir(self, visit: str) -> Path:
        """``raw/<visit_id>/`` of a visit."""
        parse_visit_id(visit)
        return self.area("raw") / visit

    def require(self, data_kind: DataKind) -> None:
        """Raise :class:`WatermarkError` unless this root holds ``data_kind`` data."""
        if self.data_kind != data_kind:
            raise WatermarkError(
                f"{self.path} holds {self.data_kind} data; refusing to write {data_kind} outputs"
            )

    def _inside(self, area: Area, relpath: str, what: str) -> Path:
        parts = relpath.split("/")
        if "\\" in relpath or ":" in relpath or any(p in ("", ".", "..") for p in parts):
            raise WatermarkError(f"invalid {what} path {relpath!r}")
        return self.area(area).joinpath(*parts)

    def output_path(self, area: Area, relpath: str) -> Path:
        """Path of an output file: ``area`` must be an output area and ``relpath`` a
        relative POSIX path that stays inside it."""
        if area not in OUTPUT_AREAS:
            raise WatermarkError(f"area {area!r} is read-only")
        return self._inside(area, relpath, "output")

    def input_path(self, area: Area, relpath: str) -> Path:
        """Path of a file in a read-only area (``raw``, ``inputs``, ``keys``); ``relpath``
        must be a relative POSIX path that stays inside it. Reading only: writes go through
        :func:`write_synthetic_input`."""
        if area not in READ_ONLY_AREAS:
            raise WatermarkError(f"area {area!r} is not an input area")
        return self._inside(area, relpath, "input")


def _check_nesting(path: Path, data_kind: DataKind) -> None:
    for parent in path.parents:
        kind = _marker_kind(parent)
        if kind is not None and kind != data_kind:
            raise WatermarkError(f"{path} lies inside a {kind} data root ({parent})")
    if path.is_dir():
        for marker in sorted(path.rglob(MARKER)):
            kind = _marker_kind(marker.parent)
            if marker.parent != path and kind != data_kind:
                raise WatermarkError(f"{path} contains a {kind} data root ({marker.parent})")


def check_watermark(data: bytes, suffix: str, data_kind: DataKind) -> None:
    """Raise :class:`WatermarkError` unless ``data`` carries the ``data_kind`` watermark.

    ``.json``: top-level object with ``"data_kind"``; ``.csv``: first column
    ``data_kind`` with that value on every row; ``.html``: ``<meta name="av-data-kind"
    content="KIND">``; ``.md``: a line ``av-data-kind: KIND``. Synthetic HTML and Markdown
    must also show the word ``SYNTHETIC`` (the visible banner). Other suffixes are refused.
    """
    suffix = suffix.lower()
    if suffix == ".json":
        try:
            doc = json.loads(data)
        except ValueError:
            raise WatermarkError("JSON output is not valid JSON") from None
        if not isinstance(doc, dict) or doc.get("data_kind") != data_kind:
            raise WatermarkError(f"JSON output lacks top-level data_kind {data_kind!r}")
    elif suffix == ".csv":
        try:
            header, rows = parse_csv(data)
        except CsvFormatError as exc:
            raise WatermarkError(f"CSV output is not strict CSV: {exc}") from None
        if header[0] != "data_kind" or any(r[0] != data_kind for r in rows):
            raise WatermarkError(f"CSV output lacks a data_kind column equal to {data_kind!r}")
    elif suffix in (".html", ".md"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise WatermarkError(f"{suffix} output is not UTF-8") from None
        if suffix == ".html":
            meta = f'<meta name="av-data-kind" content="{data_kind}">'
            ok = meta in text
        else:
            ok = f"av-data-kind: {data_kind}" in text.splitlines()
        other = "REAL" if data_kind == "SYNTHETIC" else "SYNTHETIC"
        if not ok or f"av-data-kind: {other}" in text or f'content="{other}"' in text:
            raise WatermarkError(f"{suffix} output lacks the {data_kind} marker")
        if data_kind == "SYNTHETIC" and text.count(WATERMARK_BANNER) < 2:
            raise WatermarkError("synthetic report lacks a visible SYNTHETIC banner")
    else:
        raise WatermarkError(f"no watermark rule for {suffix!r} outputs")


def write_output(
    root: DataRoot, area: Area, relpath: str, data: bytes, data_kind: DataKind
) -> Path:
    """Write one output file after checking area, root kind and the data's watermark."""
    root.require(data_kind)
    path = root.output_path(area, relpath)
    check_watermark(data, path.suffix, data_kind)
    write_bytes(path, data)
    return path


def _refuse_real_content(data: bytes, relpath: str) -> None:
    """Raise :class:`WatermarkError` if synthetic input ``data`` is marked as real."""
    suffix = Path(relpath).suffix.lower()
    if suffix == ".json":
        try:
            doc = json.loads(data)
        except ValueError:
            raise WatermarkError(f"{relpath}: not valid JSON") from None
        if isinstance(doc, dict):
            if "data_kind" in doc and doc["data_kind"] != "SYNTHETIC":
                raise WatermarkError(f"{relpath}: data_kind is not SYNTHETIC")
            if "demo" in doc and doc["demo"] is not True:
                raise WatermarkError(f"{relpath}: demo is not true")
        if Path(relpath).name == EXIT_MANIFEST and (
            not isinstance(doc, dict) or doc.get("data_kind") != "SYNTHETIC"
        ):
            raise WatermarkError(f"{relpath}: an exit manifest needs data_kind SYNTHETIC")
    elif suffix == ".csv":
        try:
            header, rows = parse_csv(data)
        except CsvFormatError as exc:
            raise WatermarkError(f"{relpath}: not strict CSV: {exc}") from None
        if "data_kind" in header:
            i = header.index("data_kind")
            if any(r[i] != "SYNTHETIC" for r in rows):
                raise WatermarkError(f"{relpath}: data_kind column is not SYNTHETIC")


def write_synthetic_input(root: DataRoot, area: Area, relpath: str, data: bytes) -> Path:
    """Write one file into a read-only area of a SYNTHETIC root (the only sanctioned way).

    For synthetic generators only: #33 ``synth-logs`` and fault injection (raw logs, exit
    manifests, ``inputs/``) and #34 ``simulate`` (``keys/`` and ``inputs/``). Refuses a
    root that is not SYNTHETIC, an area other than ``raw``, ``inputs`` or ``keys``, a path
    that leaves the area, a ``raw/`` path other than ``deviations-log.csv`` or
    ``<visit_id>/<file>``, and content marked as real: a JSON object whose ``data_kind`` is
    not ``SYNTHETIC`` or whose ``demo`` is not ``true``, an exit manifest without
    ``data_kind`` ``SYNTHETIC``, or a CSV whose ``data_kind`` column holds another value.
    Existing files are replaced (fault injection rewrites raw files).
    """
    path = _synthetic_input_path(root, area, relpath)
    _refuse_real_content(data, f"{area}/{relpath}")
    write_bytes(path, data)
    return path


def remove_synthetic_input(root: DataRoot, area: Area, relpath: str) -> bool:
    """Delete one file of a read-only area of a SYNTHETIC root (fault injection, e.g. a
    missing raw file); same root and path rules as :func:`write_synthetic_input`.
    Returns False when the file did not exist."""
    path = _synthetic_input_path(root, area, relpath)
    if not path.is_file():
        return False
    path.unlink()
    return True


def _synthetic_input_path(root: DataRoot, area: Area, relpath: str) -> Path:
    if not root.synthetic:
        raise WatermarkError(f"{root.path} holds REAL data; synthetic inputs are refused")
    path = root.input_path(area, relpath)
    if area == "raw" and relpath != DEVIATIONS_LOG:
        head, _, rest = relpath.partition("/")
        try:
            parse_visit_id(head)
        except ValueError:
            raise WatermarkError(f"invalid raw path {relpath!r}") from None
        if not rest or "/" in rest:
            raise WatermarkError(f"invalid raw path {relpath!r}")
    return path
