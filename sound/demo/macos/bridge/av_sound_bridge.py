"""JSON-lines bridge between the macOS demo app (`AVSoundDemo`) and the sound engine.

The contract is `../PROTOCOL.md` (bridge protocol version 1). Run from the repository
root:

    uv run --frozen --project sound python sound/demo/macos/bridge/av_sound_bridge.py

(`--frozen`: uv uses `sound/uv.lock` as it is and never rewrites it.)

The bridge reads one compact JSON request per line on stdin and writes exactly one
compact UTF-8 JSON response line per request on stdout, flushed. Diagnostics go to
stderr only. A bad request gives an error response, never an exit; `shutdown` and the
end of stdin exit with status 0.

Demo only: every book is synthetic (`DEMO-...`), and stores and packages live in one
temporary directory that the bridge creates outside every git work tree and removes
when it exits (`--keep-temp` keeps it), also when it is stopped with SIGTERM or SIGHUP.
Run as a script, the bridge writes no Python bytecode caches either.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    # PROTOCOL.md, "Safety rules": the bridge writes only under its own temp directory, so
    # no `__pycache__` folders for av_sound or sound/tools in the work tree. This must run
    # before the engine is imported. The app also sets PYTHONDONTWRITEBYTECODE; this
    # covers a bridge started by hand.
    sys.dont_write_bytecode = True

import argparse
import base64
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import math
import os
import platform
import random
import shutil
import signal
import stat
import tempfile
import traceback
from array import array
from collections.abc import Callable, Iterator, Mapping
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from types import FrameType, ModuleType
from typing import IO, Any, Final

from av_sound import (
    FEATURE_NAMES,
    GAP_SAMPLES,
    MIN_EVENT_SAMPLES,
    REASON_CODES,
    RENDERER_VERSION,
    SAMPLE_RATE,
    VALIDATOR_VERSION,
    AtomAudio,
    CommitRejected,
    FallbackSet,
    PackageError,
    Profile,
    Recipe,
    Reference,
    StoreEntry,
    StoreError,
    VocabularyStore,
    build_fallback,
    build_package,
    compose_message,
    composite_hash,
    distance,
    event_samples,
    features,
    load_package,
    load_separation_threshold,
    message_length,
    nearest_reference,
    nonlexical_asset,
    nonlexical_assets,
    render,
    renderer_hash,
    renderer_recipe_schema_hash,
    scan_fallback,
    scan_package,
    seal,
    self_test,
    separated,
    sum_squared_diff,
    validate,
)
from av_sound import golden as golden_mod
from av_sound.fallback import DEMO_SEED, inside_work_tree
from av_sound.features import format_fraction
from av_sound.grammar import ATOM_IDS, FAMILIES, MESSAGES, parse_atom_id, parse_message_id
from av_sound.grammar import message_id as grammar_message_id
from av_sound.nonlexical import NonlexicalAsset
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS
from av_sound.store import E_POLICY, NotFound, canonical_json, validator_code_hash
from av_sound.synthetic import synthetic_book_id, synthetic_recipes
from av_sound.wav import HEADER_SIZE, file_sha256, pcm_from_wav, wav_bytes

BRIDGE_VERSION: Final = 1
"""Bumped together with PROTOCOL.md and the Swift client."""

SOUND_ROOT: Final = Path(__file__).resolve().parents[3]
REPO_ROOT: Final = SOUND_ROOT.parent
RENDERER_VECTORS: Final = SOUND_ROOT / "testvectors" / "renderer" / "vectors.json"
COMPOSITION_VECTORS: Final = SOUND_ROOT / "testvectors" / "composition" / "vectors.json"
GOLDEN_MANIFEST: Final = REPO_ROOT / "tests" / "golden" / "manifest.json"
EXAMPLE_PACKAGE_TOOL: Final = SOUND_ROOT / "tools" / "build_example_package.py"

E_BAD_REQUEST: Final = "E_BAD_REQUEST"
E_UNKNOWN_CMD: Final = "E_UNKNOWN_CMD"

DEMO_PREFIX: Final = "DEMO-"
COMMIT_SOURCE: Final = "demo-app"
"""Store `source` of every commit the demo makes."""
STORE_LOCK_TIMEOUT_S: Final = 5.0
TAMPER_KINDS: Final[tuple[str, ...]] = ("flip_blob_byte", "edit_log_line", "truncate_log")
TAMPERED_TIMESTAMP: Final = "2000-01-01T00:00:00.000Z"
ANSWERS_PREVIEW: Final = 5
RANDOM_RECIPE_MAX_TRIES: Final = 10_000

logger = logging.getLogger("av_sound_bridge")

JsonObject = dict[str, Any]
Handler = Callable[[Mapping[str, Any]], JsonObject | None]


class ProtocolError(Exception):
    """A request that breaks the envelope or argument rules of PROTOCOL.md."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Encoding helpers


def _json_default(value: object) -> object:
    if isinstance(value, Fraction):
        return format_fraction(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set | frozenset):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def encode(message: Mapping[str, Any]) -> bytes:
    """One compact UTF-8 JSON line (with the final LF). NaN and infinities are refused."""
    text = json.dumps(
        message,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    )
    return text.encode("utf-8") + b"\n"


def _finite(value: float) -> float | None:
    """`None` for -inf (silence) and other non-finite report values."""
    return value if math.isfinite(value) else None


def _fraction_text(value: Fraction) -> str:
    return format_fraction(value)


def audio_fields(pcm: bytes | None) -> JsonObject:
    """`wav_b64`, `file_sha256` and `pcm_sha256` of int16 LE samples (all null for none)."""
    if pcm is None:
        return {"wav_b64": None, "file_sha256": None, "pcm_sha256": None}
    data = wav_bytes(pcm)
    return {
        "wav_b64": base64.b64encode(data).decode("ascii"),
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
    }


def error_fields(exc: BaseException) -> JsonObject:
    """The `error` object: class name, engine reason code (or null) and message."""
    code = getattr(exc, "code", None)
    if isinstance(exc, KeyError) and exc.args:
        message = str(exc.args[0])
    else:
        message = str(exc) or type(exc).__name__
    error: JsonObject = {
        "type": type(exc).__name__,
        "code": code if isinstance(code, str) else None,
        "message": message,
    }
    if isinstance(exc, CommitRejected):
        error["details"] = exc.result.to_dict()
    return error


def failure(request_id: int | None, exc: BaseException) -> JsonObject:
    return {"id": request_id, "ok": False, "error": error_fields(exc)}


def _mismatch(item: str, field: str, expected: object, actual: object) -> str:
    """Same text as `av_sound.golden.Mismatch`."""
    return f"{item}: {field}: expected {expected!r}, got {actual!r}"


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-standard JSON constant {name}")


def _record(line: bytes) -> dict[str, Any] | None:
    """A store log line as a dict, or `None` if it is not a JSON object."""
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# Argument helpers


def _require(args: Mapping[str, Any], name: str) -> object:
    if name not in args:
        raise ProtocolError(E_BAD_REQUEST, f"missing argument {name!r}")
    return args[name]


def _text(args: Mapping[str, Any], name: str) -> str:
    value = _require(args, name)
    if not isinstance(value, str):
        raise ProtocolError(E_BAD_REQUEST, f"argument {name!r} must be a string")
    return value


def _optional_text(args: Mapping[str, Any], name: str) -> str | None:
    """An optional argument: absent or null gives `None`, else it must be a string."""
    value = args.get(name)
    if value is not None and not isinstance(value, str):
        raise ProtocolError(E_BAD_REQUEST, f"argument {name!r} must be a string or null")
    return value


def _nullable_text(args: Mapping[str, Any], name: str) -> str | None:
    """A required argument whose value is a string or null (absent is `E_BAD_REQUEST`)."""
    _require(args, name)
    return _optional_text(args, name)


def _flag(args: Mapping[str, Any], name: str, default: bool) -> bool:
    value = args.get(name, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ProtocolError(E_BAD_REQUEST, f"argument {name!r} must be true or false")
    return value


def _integer(args: Mapping[str, Any], name: str) -> int:
    value = _require(args, name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(E_BAD_REQUEST, f"argument {name!r} must be an integer")
    return value


def _array(args: Mapping[str, Any], name: str, *, optional: bool = False) -> list[object]:
    value = args.get(name) if optional else _require(args, name)
    if value is None and optional:
        return []
    if not isinstance(value, list):
        raise ProtocolError(E_BAD_REQUEST, f"argument {name!r} must be a list")
    return value


def _object(args: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = _require(args, name)
    if not isinstance(value, dict):
        raise ProtocolError(E_BAD_REQUEST, f"argument {name!r} must be a JSON object")
    return value


def _indices(args: Mapping[str, Any], name: str) -> list[int]:
    """An optional list of integers (absent or null: empty)."""
    values = _array(args, name, optional=True)
    for i, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(E_BAD_REQUEST, f"{name}[{i}] must be an integer")
    return values  # type: ignore[return-value]


def _profile(args: Mapping[str, Any]) -> Profile:
    """`P1`, `P2` or `P3`; anything else raises the engine's `ValueError`."""
    return Profile(_text(args, "profile"))


def _recipe(args: Mapping[str, Any], name: str) -> Recipe:
    """A recipe object. A value that is not a JSON object is a bad request; the engine
    checks the object (`RecipeError`: `E_SCHEMA`, `E_DOMAIN`)."""
    return Recipe.from_dict(_object(args, name))


def demo_book_id(value: object) -> str:
    """Refuse every book ID that is not a synthetic `DEMO-` ID (safety rule)."""
    if not isinstance(value, str):
        raise ProtocolError(E_BAD_REQUEST, "book_id must be a string")
    if not value.startswith(DEMO_PREFIX):
        raise StoreError(
            E_POLICY,
            f"book ID {value!r} refused: the demo uses only synthetic books whose IDs "
            f"start with {DEMO_PREFIX}",
        )
    return value


@lru_cache(maxsize=4096)
def _rendered_pcm_sha256(canonical_recipe: str, profile: str) -> str:
    """Waveform hash of a recipe (raises `OverflowError` if it clips)."""
    return render(Recipe.from_json(canonical_recipe), profile).pcm_sha256


def _atom_reference(value: object, where: str) -> tuple[str, Recipe, str | None]:
    """`{"ref_id", "recipe", "book_id"?}` -> ref ID, recipe and optional book ID."""
    if not isinstance(value, dict):
        raise ProtocolError(E_BAD_REQUEST, f"{where} must be an object with ref_id and recipe")
    ref_id = value.get("ref_id")
    if not isinstance(ref_id, str):
        raise ProtocolError(E_BAD_REQUEST, f"{where}.ref_id must be a string")
    if "recipe" not in value:
        raise ProtocolError(E_BAD_REQUEST, f"{where}.recipe is missing")
    if not isinstance(value["recipe"], dict):
        raise ProtocolError(E_BAD_REQUEST, f"{where}.recipe must be a JSON object")
    book_id = value.get("book_id")
    if book_id is not None:
        book_id = demo_book_id(book_id)
    return ref_id, Recipe.from_dict(value["recipe"]), book_id


def references(values: list[object], profile: Profile, name: str) -> list[Reference]:
    """Validator references for atom references; the bridge renders each recipe."""
    out: list[Reference] = []
    for i, item in enumerate(values):
        ref_id, recipe, _ = _atom_reference(item, f"{name}[{i}]")
        digest = _rendered_pcm_sha256(recipe.canonical_json(), profile.value)
        out.append(Reference(ref_id, recipe, digest, profile))
    return out


def _atom_audio(value: object, profile: Profile, book_id: str | None, where: str) -> AtomAudio:
    ref_id, recipe, own_book = _atom_reference(value, where)
    rendered = render(recipe, profile)
    book = own_book if own_book is not None else book_id
    return AtomAudio(ref_id, profile, rendered.pcm, book_id=book)


def _pattern_pcm(n_samples: int, mul: int, add: int) -> bytes:
    """Composition-vector pattern: sample[i] = (mul * i + add) mod 65535 - 32767."""
    samples = array("h", ((mul * i + add) % 65_535 - 32_767 for i in range(n_samples)))
    if sys.byteorder == "big":  # pragma: no cover - little-endian hosts
        samples.byteswap()
    return samples.tobytes()


def _asset_fields(asset: NonlexicalAsset) -> JsonObject:
    return {
        "id": asset.id,
        "kind": asset.kind,
        "profile": None if asset.profile is None else asset.profile.value,
        "n_samples": asset.n_samples,
        "duration_ms": asset.duration_ms,
        "peak_dbfs": _finite(asset.peak_dbfs),
        "rms_dbfs": _finite(asset.rms_dbfs),
        "active_rms_dbfs": _finite(asset.active_rms_dbfs),
        "pcm_sha256": asset.pcm_sha256,
        "file_sha256": asset.file_sha256,
        "description": asset.description,
    }


def _entry_fields(entry: StoreEntry) -> JsonObject:
    return {
        "atom_id": entry.atom_id,
        "commit_index": entry.commit_index,
        "pcm_sha256": entry.pcm_sha256,
        "file_sha256": entry.file_sha256,
        "recipe": entry.recipe.to_dict(),
    }


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        with contextlib.suppress(OSError):
            if path.is_file() and not path.is_symlink():
                os.chmod(path, stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)


def remove_tree(root: Path) -> None:
    """Remove a temp tree, including the read-only store blobs and logs."""
    if root.exists():
        _make_writable(root)
        shutil.rmtree(root, ignore_errors=True)


def _rewrite(path: Path, data: bytes) -> None:
    """Replace the bytes of a (read-only) store file in place, keeping its mode."""
    mode = stat.S_IMODE(path.stat().st_mode)
    os.chmod(path, mode | stat.S_IWUSR)
    try:
        with open(path, "wb") as f:
            f.write(data)
    finally:
        os.chmod(path, mode)


def _intact_blob(blob: Path, pcm_hash: str) -> bytearray | None:
    """The bytes of a canonical WAV blob whose samples hash to `pcm_hash`, else `None`
    (missing, not canonical, no samples, or already damaged)."""
    try:
        data = blob.read_bytes()
        pcm = pcm_from_wav(data)
    except (OSError, ValueError):
        return None
    if not pcm or hashlib.sha256(pcm).hexdigest() != pcm_hash:
        return None
    return bytearray(data)


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved == root or root in resolved.parents


@contextlib.contextmanager
def _temp_under(directory: Path) -> Iterator[None]:
    """Point `tempfile` at `directory` so engine-internal temp dirs stay in the bridge root."""
    previous = tempfile.tempdir
    tempfile.tempdir = str(directory)
    try:
        yield
    finally:
        tempfile.tempdir = previous


def _load_example_tool() -> ModuleType:
    """`sound/tools/build_example_package.py` (the DEMO package recipe), imported by path."""
    spec = importlib.util.spec_from_file_location(
        "av_sound_demo_build_example_package", EXAMPLE_PACKAGE_TOOL
    )
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"cannot load {EXAMPLE_PACKAGE_TOOL.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The bridge


class Bridge:
    """Dispatches protocol requests to the engine and keeps the per-process state.

    - `temp_base`: directory in which the bridge creates its temp root (default: the
      system temp directory). The root must be outside every git work tree.
    - `keep_temp`: keep the temp root when the bridge closes.
    """

    def __init__(
        self, *, temp_base: str | os.PathLike[str] | None = None, keep_temp: bool = False
    ) -> None:
        self._temp_base = None if temp_base is None else Path(temp_base)
        self._keep_temp = keep_temp
        self._root: Path | None = None
        self._store: VocabularyStore | None = None
        self._store_root: Path | None = None
        self._cache: dict[str, JsonObject] = {}
        self._fallback: FallbackSet | None = None
        self._fallback_hash: str | None = None
        self._package_work: Path | None = None
        self._tool: ModuleType | None = None
        self.closed = False
        self.commands: dict[str, Handler] = {
            "hello": self.cmd_hello,
            "self_test": self.cmd_self_test,
            "render": self.cmd_render,
            "random_recipe": self.cmd_random_recipe,
            "features": self.cmd_features,
            "distance": self.cmd_distance,
            "validate": self.cmd_validate,
            "nearest": self.cmd_nearest,
            "grammar": self.cmd_grammar,
            "compose": self.cmd_compose,
            "composite_hash": self.cmd_composite_hash,
            "synthetic_book": self.cmd_synthetic_book,
            "nonlexical_list": self.cmd_nonlexical_list,
            "nonlexical_get": self.cmd_nonlexical_get,
            "vectors_check": self.cmd_vectors_check,
            "golden_check": self.cmd_golden_check,
            "store_reset": self.cmd_store_reset,
            "store_create": self.cmd_store_create,
            "store_commit": self.cmd_store_commit,
            "store_list": self.cmd_store_list,
            "store_records": self.cmd_store_records,
            "store_freeze": self.cmd_store_freeze,
            "store_verify": self.cmd_store_verify,
            "store_tamper": self.cmd_store_tamper,
            "fallback_demo": self.cmd_fallback_demo,
            "fallback_scan": self.cmd_fallback_scan,
            "package_demo": self.cmd_package_demo,
            "shutdown": self.cmd_shutdown,
        }

    # -- envelope -------------------------------------------------------------

    def handle_line(self, line: bytes | str) -> JsonObject:
        """Parse one request line and return its response object."""
        try:
            text = line.decode("utf-8") if isinstance(line, bytes) else line
            request = json.loads(text, parse_constant=_reject_constant)
        except Exception as exc:  # not UTF-8, not JSON, NaN, nesting limits
            return failure(None, ProtocolError(E_BAD_REQUEST, f"request is not JSON: {exc}"))
        return self.dispatch(request)

    def dispatch(self, request: object) -> JsonObject:
        """Run one decoded request; every failure becomes an error response."""
        if not isinstance(request, dict):
            return failure(None, ProtocolError(E_BAD_REQUEST, "a request is a JSON object"))
        request_id = request.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1:
            return failure(None, ProtocolError(E_BAD_REQUEST, "id must be a positive integer"))
        cmd = request.get("cmd")
        if not isinstance(cmd, str):
            return failure(request_id, ProtocolError(E_BAD_REQUEST, "cmd must be a string"))
        args = request.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return failure(request_id, ProtocolError(E_BAD_REQUEST, "args must be an object"))
        handler = self.commands.get(cmd)
        if handler is None:
            return failure(request_id, ProtocolError(E_UNKNOWN_CMD, f"unknown command {cmd!r}"))
        try:
            result = handler(args)
        except Exception as exc:
            logger.info("request %d (%s) failed: %s: %s", request_id, cmd, type(exc).__name__, exc)
            logger.debug("%s", traceback.format_exc())
            return failure(request_id, exc)
        return {"id": request_id, "ok": True, "result": result}

    def close(self) -> None:
        """Remove the temp root (unless kept)."""
        if self._root is not None and not self._keep_temp:
            remove_tree(self._root)
        self._root = None
        self._store = None
        self._store_root = None
        self._package_work = None

    # -- temp storage ---------------------------------------------------------

    def temp_root(self) -> Path:
        """The bridge's own temp directory, created on first use outside any work tree."""
        if self._root is None:
            if self._temp_base is not None:
                self._temp_base.mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix="av-sound-bridge-", dir=self._temp_base))
            root = root.resolve()
            if inside_work_tree(root):
                remove_tree(root)
                raise StoreError(
                    E_POLICY,
                    "the temp directory is inside a git work tree; point TMPDIR elsewhere",
                )
            self._root = root
            logger.info("temp root %s", root)
        return self._root

    def _new_store(self) -> VocabularyStore:
        if self._store_root is not None:
            remove_tree(self._store_root)
        root = Path(tempfile.mkdtemp(prefix="store-", dir=self.temp_root())).resolve()
        self._store_root = root
        self._store = VocabularyStore(root, lock_timeout=STORE_LOCK_TIMEOUT_S)
        return self._store

    def store(self) -> VocabularyStore:
        """The current demo store (a fresh one on first use)."""
        return self._store if self._store is not None else self._new_store()

    @property
    def store_root(self) -> Path | None:
        return self._store_root

    # -- engine facts ---------------------------------------------------------

    def cmd_hello(self, args: Mapping[str, Any]) -> JsonObject:
        if "hello" not in self._cache:
            threshold = load_separation_threshold()
            self._cache["hello"] = {
                "bridge_version": BRIDGE_VERSION,
                "renderer_version": RENDERER_VERSION,
                "renderer_hash": renderer_hash(),
                "renderer_recipe_schema_hash": renderer_recipe_schema_hash(),
                "validator_version": VALIDATOR_VERSION,
                "validator_hash": validator_code_hash(),
                "python": platform.python_version(),
                "numpy": importlib.metadata.version("numpy"),
                "sample_rate": SAMPLE_RATE,
                "gap_samples": GAP_SAMPLES,
                "threshold": _fraction_text(threshold),
                "threshold_float": float(threshold),
                "profiles": [{"id": p.value, "f0_hz": p.f0_hz} for p in Profile],
                "domain": {
                    "total_ms": list(TOTAL_MS),
                    "pitches": list(PITCHES),
                    "rhythm_weights": list(RHYTHM_WEIGHTS),
                    "gaps_ms": list(GAPS_MS),
                    "amplitudes": list(AMPLITUDES),
                },
                "reason_codes": list(REASON_CODES),
                "feature_names": list(FEATURE_NAMES),
            }
        return self._cache["hello"]

    def cmd_self_test(self, args: Mapping[str, Any]) -> JsonObject:
        try:
            self_test()
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "renderer self-test passed: the pinned vectors reproduce"}

    # -- motifs ---------------------------------------------------------------

    def cmd_render(self, args: Mapping[str, Any]) -> JsonObject:
        recipe = _recipe(args, "recipe")
        rendered = render(recipe, _profile(args))
        layout = rendered.timing
        return {
            "recipe": recipe.to_dict(),
            "recipe_sha256": recipe.sha256(),
            "profile": rendered.profile.value,
            "n_samples": rendered.n_samples,
            "duration_ms": recipe.total_ms,
            "event_samples": list(layout.event_samples),
            "event_onsets": list(layout.event_onsets),
            "gap_samples": list(layout.gap_samples),
            "peak": rendered.peak,
            "peak_dbfs": _finite(rendered.peak_dbfs),
            "rms": rendered.rms,
            "short_event": rendered.short_event,
            "overflow": rendered.overflow,
            "nonfinite": rendered.nonfinite,
            "renderer_version": rendered.renderer_version,
            **audio_fields(None if rendered.overflow else rendered.pcm),
        }

    def cmd_random_recipe(self, args: Mapping[str, Any]) -> JsonObject:
        seed = _integer(args, "seed")
        admissible_only = _flag(args, "admissible_only", True)
        rng = random.Random(seed)
        for _ in range(RANDOM_RECIPE_MAX_TRIES):
            total_ms = rng.choice(TOTAL_MS)
            pitches = (rng.choice(PITCHES), rng.choice(PITCHES), rng.choice(PITCHES))
            weights = (
                rng.choice(RHYTHM_WEIGHTS),
                rng.choice(RHYTHM_WEIGHTS),
                rng.choice(RHYTHM_WEIGHTS),
            )
            gaps = (rng.choice(GAPS_MS), rng.choice(GAPS_MS))
            amplitudes = (rng.choice(AMPLITUDES), rng.choice(AMPLITUDES), rng.choice(AMPLITUDES))
            if admissible_only and min(event_samples(total_ms, weights, gaps)) < MIN_EVENT_SAMPLES:
                continue
            recipe = Recipe(total_ms, pitches, weights, gaps, amplitudes)
            return {"recipe": recipe.to_dict()}
        raise RuntimeError("no admissible recipe drawn")  # pragma: no cover - ~90% pass

    def cmd_features(self, args: Mapping[str, Any]) -> JsonObject:
        values = features(_recipe(args, "recipe"))
        return {
            "names": list(FEATURE_NAMES),
            "exact": [_fraction_text(v) for v in values],
            "values": [float(v) for v in values],
        }

    def cmd_distance(self, args: Mapping[str, Any]) -> JsonObject:
        a, b = _recipe(args, "a"), _recipe(args, "b")
        return {
            "distance": distance(a, b),
            "sum_sq": _fraction_text(sum_squared_diff(a, b)),
            "separated": separated(a, b, load_separation_threshold()),
        }

    def cmd_validate(self, args: Mapping[str, Any]) -> JsonObject:
        # Any JSON value: the validator gives every candidate a verdict (a non-recipe value
        # is a normal E_SCHEMA rejection), so `candidate` is never a bad request.
        candidate = _require(args, "candidate")
        profile = _profile(args)
        committed = references(_array(args, "committed", optional=True), profile, "committed")
        threshold = _optional_text(args, "threshold")  # a decimal such as "0.1"
        use_reserved = _flag(args, "use_reserved", True)
        result = validate(
            candidate,  # type: ignore[arg-type]
            profile,
            committed,
            reserved=None if use_reserved else (),
            threshold=threshold,
        )
        return result.to_dict()

    def cmd_nearest(self, args: Mapping[str, Any]) -> JsonObject | None:
        candidate = _recipe(args, "candidate")
        profile = _profile(args)
        refs = references(_array(args, "committed"), profile, "committed")
        nearest = nearest_reference(candidate, refs, profile=profile)
        if nearest is None:
            return None
        return {
            "ref_id": nearest.ref_id,
            "index": nearest.index,
            "distance": nearest.distance,
            "sum_sq": _fraction_text(nearest.sum_sq),
        }

    # -- grammar and messages -------------------------------------------------

    def cmd_grammar(self, args: Mapping[str, Any]) -> JsonObject:
        if "grammar" not in self._cache:
            self._cache["grammar"] = {
                "families": list(FAMILIES),
                "atom_ids": list(ATOM_IDS),
                "messages": [
                    {
                        "message_id": m.message_id,
                        "family": m.family,
                        "action": m.action.atom_id,
                        "referent": m.referent.atom_id,
                        "status": m.status,
                        "training_wave": m.training_wave,
                        "heldout_set": m.heldout_set,
                        "is_heldout": m.is_heldout,
                    }
                    for m in MESSAGES
                ],
            }
        return self._cache["grammar"]

    def _message_atoms(self, args: Mapping[str, Any]) -> tuple[AtomAudio, AtomAudio]:
        profile = _profile(args)
        book_id = _optional_text(args, "book_id")
        if book_id is not None:
            book_id = demo_book_id(book_id)
        action = _atom_audio(_require(args, "action"), profile, book_id, "action")
        referent = _atom_audio(_require(args, "referent"), profile, book_id, "referent")
        return action, referent

    def cmd_compose(self, args: Mapping[str, Any]) -> JsonObject:
        action, referent = self._message_atoms(args)
        message = compose_message(action, referent)  # refuses held-out messages
        return {
            "message_id": message.message_id,
            "n_samples": message.n_samples,
            "duration_s": message.duration_s,
            "action_samples": message.action_samples,
            "referent_samples": message.referent_samples,
            "referent_onset": message.referent_onset,
            **audio_fields(message.pcm),
        }

    def cmd_composite_hash(self, args: Mapping[str, Any]) -> JsonObject:
        action, referent = self._message_atoms(args)
        digest = composite_hash(action, referent)  # role, family, profile, book, length
        a, r = parse_atom_id(action.atom_id), parse_atom_id(referent.atom_id)
        message_id = grammar_message_id(a.family, a.index, r.index)
        n_samples = message_length(action, referent)
        return {
            "message_id": message_id,
            "composite_sha256": digest,
            "n_samples": n_samples,
            "duration_s": n_samples / SAMPLE_RATE,
            "is_heldout": parse_message_id(message_id).is_heldout,
        }

    def cmd_synthetic_book(self, args: Mapping[str, Any]) -> JsonObject:
        profile = _profile(args)
        key = f"synthetic_book/{profile.value}"
        if key not in self._cache:
            atoms = []
            for atom_id, recipe in synthetic_recipes(profile).items():
                rendered = render(recipe, profile)
                atoms.append(
                    {
                        "atom_id": atom_id,
                        "recipe": recipe.to_dict(),
                        "pcm_sha256": rendered.pcm_sha256,
                        "n_samples": rendered.n_samples,
                    }
                )
            self._cache[key] = {"book_id": synthetic_book_id(profile), "atoms": atoms}
        return self._cache[key]

    # -- nonlexical assets ----------------------------------------------------

    def cmd_nonlexical_list(self, args: Mapping[str, Any]) -> JsonObject:
        if "nonlexical_list" not in self._cache:
            assets = [_asset_fields(a) for a in nonlexical_assets()]
            self._cache["nonlexical_list"] = {"assets": assets}
        return self._cache["nonlexical_list"]

    def cmd_nonlexical_get(self, args: Mapping[str, Any]) -> JsonObject:
        asset = nonlexical_asset(_text(args, "id"))  # KeyError for an unknown ID
        return {**_asset_fields(asset), **audio_fields(asset.pcm)}

    # -- reference checks -----------------------------------------------------

    def cmd_vectors_check(self, args: Mapping[str, Any]) -> JsonObject:
        """Re-reads and re-checks the vector files on every request (never cached)."""
        renderer = check_renderer_vectors(RENDERER_VECTORS)
        composition = check_composition_vectors(COMPOSITION_VECTORS)
        return {
            "renderer": renderer,
            "composition": composition,
            "ok": not renderer["mismatches"] and not composition["mismatches"],
        }

    def cmd_golden_check(self, args: Mapping[str, Any]) -> JsonObject:
        """Re-reads the manifest and recomputes every item on every request (never cached)."""
        manifest = golden_mod.load_manifest(GOLDEN_MANIFEST)
        with _temp_under(self.temp_root()):  # the store round trips use a temp dir
            items = golden_mod.compute_items(golden_mod.specs_of(manifest))
        mismatches = golden_mod.verify_manifest(manifest, items)
        return {
            "items": len(manifest["items"]),
            "mismatches": [str(m) for m in mismatches],
            "ok": not mismatches,
            "digest": golden_mod.digests(items)["all"],
        }

    # -- vocabulary store -----------------------------------------------------

    def cmd_store_reset(self, args: Mapping[str, Any]) -> JsonObject:
        self._new_store()
        assert self._store_root is not None
        return {"root": str(self._store_root)}

    def cmd_store_create(self, args: Mapping[str, Any]) -> JsonObject:
        book_id = demo_book_id(_require(args, "book_id"))
        profile = _profile(args)
        threshold = _optional_text(args, "threshold")
        head = self.store().create_book(book_id, profile, kind="synthetic", threshold=threshold)
        return {"book_id": book_id, "chain_head": head}

    def cmd_store_commit(self, args: Mapping[str, Any]) -> JsonObject:
        book_id = demo_book_id(_require(args, "book_id"))
        atom_id = _text(args, "atom_id")
        label = _nullable_text(args, "semantic_label")  # required, may be null
        recipe = _object(args, "recipe")  # the store validates it (CommitRejected)
        store = self.store()
        entry, head = store.commit(book_id, atom_id, label, recipe, source=COMMIT_SOURCE)
        outcome = str(store.records(book_id)[-1]["event"])  # "commit" or "recommit_noop"
        return {"entry": _entry_fields(entry), "chain_head": head, "outcome": outcome}

    def cmd_store_list(self, args: Mapping[str, Any]) -> JsonObject:
        book_id = demo_book_id(_require(args, "book_id"))
        store = self.store()
        info = store.book(book_id)
        return {
            "entries": [_entry_fields(e) for e in store.list(book_id)],
            "chain_head": info.chain_head,
            "frozen": info.frozen,
            "void": info.void,
        }

    def cmd_store_records(self, args: Mapping[str, Any]) -> JsonObject:
        book_id = demo_book_id(_require(args, "book_id"))
        return {"records": self.store().records(book_id)}

    def cmd_store_freeze(self, args: Mapping[str, Any]) -> JsonObject:
        book_id = demo_book_id(_require(args, "book_id"))
        return {"chain_head": self.store().freeze(book_id)}

    def cmd_store_verify(self, args: Mapping[str, Any]) -> JsonObject:
        book_id = demo_book_id(_require(args, "book_id"))
        expected_head = _optional_text(args, "expected_head")
        report = self.store().verify(book_id, expected_head=expected_head)
        return {
            "ok": report.ok,
            "issues": [
                {"code": i.code, "line": i.seq, "message": i.message} for i in report.issues
            ],
        }

    def cmd_store_tamper(self, args: Mapping[str, Any]) -> JsonObject:
        book_id = demo_book_id(_require(args, "book_id"))
        kind = _text(args, "kind")
        if kind not in TAMPER_KINDS:
            raise ProtocolError(E_BAD_REQUEST, f"kind must be one of {list(TAMPER_KINDS)}")
        store = self._store
        root = self._own_store_root(store)
        assert store is not None
        log = store.log_path(book_id)
        self._check_target(log, root)
        if not log.is_file():
            raise NotFound(f"no book {book_id} in the demo store")
        lines = log.read_bytes().splitlines(keepends=True)
        if kind == "flip_blob_byte":
            return {"done": self._flip_blob_byte(store, root, book_id, lines)}
        if kind == "edit_log_line":
            return {"done": self._edit_log_line(log, lines)}
        return {"done": self._truncate_log(store, book_id, log, lines)}

    def _own_store_root(self, store: VocabularyStore | None) -> Path:
        """The bridge's own temp store root; anything else is refused."""
        root = self._store_root
        refused = StoreError(
            E_POLICY, "store_tamper only damages the bridge's own temp store; refused"
        )
        if store is None or root is None or self._root is None:
            raise refused
        resolved = Path(store.root).resolve()
        if resolved != root or self._root not in root.parents or inside_work_tree(root):
            raise refused
        return root

    @staticmethod
    def _check_target(path: Path, root: Path) -> None:
        if path.is_symlink() or not _inside(path, root):
            raise StoreError(E_POLICY, f"{path.name} is outside the bridge's temp store; refused")

    def _flip_blob_byte(
        self, store: VocabularyStore, root: Path, book_id: str, lines: list[bytes]
    ) -> str:
        """Flips one bit in the samples of the book's first intact committed blob.

        PROTOCOL.md, "Damaged books": a repeated tamper never repairs the damage. A blob
        that is already damaged is never flipped again, since a second flip of the same
        bit would undo the first: a repeated request damages the next intact blob of the
        book (log order) and is refused (`ValueError`, nothing changed) when none is
        left. Blobs are content-addressed and shared by the whole store
        (`blobs/<pcm_sha256>.wav`), so the flip also damages every other book of the
        store that holds the same waveform; the text names them. The store repairs a
        damaged blob on the next write of the same waveform (a `store_commit` to an intact
        book quarantines the damaged file and writes the correct bytes), which the text
        says when other books share the blob.
        """
        commits = [
            (str(r.get("atom_id")), pcm_hash)
            for r in map(_record, lines)
            if r is not None
            and r.get("event") == "commit"
            and isinstance(pcm_hash := r.get("pcm_sha256"), str)
        ]
        if not commits:
            raise ValueError(f"{book_id} has no committed atom whose blob could be damaged")
        tried: set[str] = set()
        for atom_id, pcm_hash in commits:
            if pcm_hash in tried:
                continue
            tried.add(pcm_hash)
            try:
                blob = store.blob_path(pcm_hash)
            except ValueError:  # a damaged log line: no blob name
                continue
            self._check_target(blob, root)
            data = _intact_blob(blob, pcm_hash)
            if data is None:
                continue  # already damaged or missing: another flip could undo the damage
            offset = HEADER_SIZE + (len(data) - HEADER_SIZE) // 2  # a byte of the samples
            data[offset] ^= 0x01
            _rewrite(blob, bytes(data))
            what = (
                f"flipped one bit of byte {offset} in the blob of {atom_id} in {book_id}; "
                "store_verify reports E_BLOB_HASH, and the other store commands refuse the book"
            )
            shared = self._books_holding(store, pcm_hash, book_id)
            if shared:
                what += (
                    f". The store shares this blob: {', '.join(shared)} "
                    f"{'holds' if len(shared) == 1 else 'hold'} the same waveform and "
                    f"{'is' if len(shared) == 1 else 'are'} damaged too. A later store_commit "
                    "of this waveform to an intact book of the store replaces the damaged blob "
                    "(the store quarantines it), which repairs every book that holds it"
                )
            return what
        raise ValueError(
            f"every committed blob of {book_id} is already damaged; flipping one again "
            "could undo the damage, so nothing was changed"
        )

    @staticmethod
    def _books_holding(store: VocabularyStore, pcm_hash: str, book_id: str) -> list[str]:
        """The other books of `store` with a commit of the waveform `pcm_hash` (read from
        their raw logs: a damaged book is listed too)."""
        books = []
        for other in store.books():
            if other == book_id:
                continue
            try:
                lines = store.log_path(other).read_bytes().splitlines()
            except OSError:
                continue
            for line in lines:
                record = _record(line)
                if (
                    record is not None
                    and record.get("event") == "commit"
                    and record.get("pcm_sha256") == pcm_hash
                ):
                    books.append(other)
                    break
        return books

    @staticmethod
    def _edit_log_line(log: Path, lines: list[bytes]) -> str:
        if not lines:
            raise ValueError("the log is empty")
        records = [_record(line) for line in lines]
        commits = [i for i, r in enumerate(records) if r is not None and r.get("event") == "commit"]
        target = commits[-1] if commits else len(lines) - 1
        line, record = lines[target], records[target]
        edited = b""
        if record is not None and record.get("timestamp") != TAMPERED_TIMESTAMP:
            record["timestamp"] = TAMPERED_TIMESTAMP
            edited = canonical_json(record) + b"\n"
            what = f"changed the timestamp of log line {target} to {TAMPERED_TIMESTAMP}"
        if not edited or edited == line:
            middle = len(line.rstrip(b"\n")) // 2
            edited = line[:middle] + bytes([line[middle] ^ 0x01]) + line[middle + 1 :]
            what = f"changed one byte of log line {target}"
        _rewrite(log, b"".join([*lines[:target], edited, *lines[target + 1 :]]))
        return what

    @staticmethod
    def _truncate_log(store: VocabularyStore, book_id: str, log: Path, lines: list[bytes]) -> str:
        """Removes the last log line and says how the damage can be detected.

        PROTOCOL.md, "Damaged books": a cut that leaves at least one record and does not
        remove the record that closed the book is self-consistent, and only an
        `expected_head` from before the cut detects it (`E_ANCHOR`). Cutting the freeze
        (or void) record leaves its marker without a record (`E_MARKER`), and cutting the
        only record leaves an empty log (`E_EVENT`): the store's own checks, which every
        reader runs, then find the damage. The text names what this cut left, from the
        same checks (no re-rendering).
        """
        if not lines:
            raise ValueError(f"the log of {book_id} is already empty")
        record = _record(lines[-1])
        event = record.get("event") if record is not None else None
        _rewrite(log, b"".join(lines[:-1]))
        what = f"removed the last log line (line {len(lines) - 1}"
        what += f", {event}) of {book_id}" if isinstance(event, str) else f") of {book_id}"
        codes = sorted({issue.code for issue in store.verify(book_id, rerender=False).issues})
        if codes:
            return (
                f"{what}; store_verify reports {', '.join(codes)} even without expected_head, "
                "and the other store commands refuse the book"
            )
        return (
            f"{what}; the shorter log is self-consistent: the other store commands accept "
            "it, and only store_verify with expected_head = the chain head before the cut "
            "detects it (E_ANCHOR)"
        )

    # -- fallback -------------------------------------------------------------

    def fallback_set(self) -> FallbackSet:
        """The public DEMO fallback set (built once per process)."""
        if self._fallback is None:
            self._fallback = build_fallback(DEMO_SEED)
            self._fallback_hash = self._fallback.fallback_bank_hash
        return self._fallback

    def cmd_fallback_demo(self, args: Mapping[str, Any]) -> JsonObject:
        profile = _profile(args)
        fset = self.fallback_set()
        bank, book = fset.bank(profile), fset.book(profile)
        return {
            "seed_label": DEMO_SEED,
            "fallback_bank_hash": self._fallback_hash,
            "bank": [
                {"index": e.index, "recipe": e.recipe.to_dict(), "pcm_sha256": e.pcm_sha256}
                for e in bank
            ],
            "book": [
                {"atom_id": a.atom_id, "recipe": a.recipe.to_dict(), "pcm_sha256": a.pcm_sha256}
                for a in book
            ],
        }

    def cmd_fallback_scan(self, args: Mapping[str, Any]) -> JsonObject:
        profile = _profile(args)
        book = references(_array(args, "book"), profile, "book")
        used = _indices(args, "used")  # the engine refuses an index outside the bank
        fset = self.fallback_set()
        result = scan_fallback(fset.bank(profile), book, used=used, threshold=fset.threshold)
        return result.to_dict()

    # -- package --------------------------------------------------------------

    def cmd_package_demo(self, args: Mapping[str, Any]) -> JsonObject:
        """Builds a fresh package on every request (never cached) in a new directory, then
        removes the package of the previous request. A failed build keeps the previous one."""
        work = Path(tempfile.mkdtemp(prefix="package-", dir=self.temp_root())).resolve()
        try:
            result = self._build_package_demo(work)
        except BaseException:
            remove_tree(work)
            raise
        if self._package_work is not None:
            remove_tree(self._package_work)
        self._package_work = work
        return result

    def _build_package_demo(self, work: Path) -> JsonObject:
        tool = self._example_tool()
        out = work / "package-demo"
        with _temp_under(work):
            store = tool.demo_store_book(work / "store")
            build_package(store, tool.BOOK_ID, out)
            package_sha256 = seal(
                out,
                permutation=tool.A_UNIT / "permutation.json",
                allocation_extras=tool.A_ALLOCATION,
            )
        try:
            load_package(out)
            loader_ok = True
        except PackageError as exc:
            logger.warning("package_demo: load_package refused the package: %s", exc)
            loader_ok = False
        leak = scan_package(out)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        answers = json.loads((out / "answers.json").read_text(encoding="utf-8"))
        files = [
            {"path": rel, "sha256": entry["sha256"], "bytes": entry["bytes"]}
            for rel, entry in sorted(manifest["files"].items())
        ]

        def wavs(folder: str) -> int:
            return sum(f["path"].startswith(folder) and f["path"].endswith(".wav") for f in files)

        return {
            "package_sha256": package_sha256,
            "files": files,
            "counts": {
                "atom_wavs": wavs("atoms/"),
                "message_wavs": wavs("messages/"),
                "heldout_ids": len(answers["heldout_message_ids"]),
            },
            "loader_ok": loader_ok,
            "leak_report": leak.to_dict(),
            "answers_preview": answers["messages"][:ANSWERS_PREVIEW],
            "dir": str(out),
        }

    def _example_tool(self) -> ModuleType:
        """The example-package tool, imported once per process."""
        if self._tool is None:
            self._tool = _load_example_tool()
        return self._tool

    # -- lifecycle ------------------------------------------------------------

    def cmd_shutdown(self, args: Mapping[str, Any]) -> JsonObject:
        self.closed = True
        return {}


# ---------------------------------------------------------------------------
# Reference-vector re-checks


def check_renderer_vectors(path: Path) -> JsonObject:
    """Re-render every renderer reference vector and compare every recorded field."""
    data = json.loads(path.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    pins = {
        "renderer_version": RENDERER_VERSION,
        "renderer_hash": renderer_hash(),
        "renderer_recipe_schema_hash": renderer_recipe_schema_hash(),
    }
    for key, pinned in pins.items():
        if data.get(key) != pinned:
            mismatches.append(_mismatch("renderer/<header>", key, data.get(key), pinned))
    checked = 0
    for vector in data["vectors"]:
        checked += 1
        item = f"renderer/{vector.get('name')}/{vector.get('profile')}"
        try:
            rendered = render(Recipe.from_dict(vector["recipe"]), vector["profile"])
            actual_fields: dict[str, object] = {
                "n_samples": rendered.n_samples,
                "event_samples": list(rendered.event_samples),
                "event_onsets": list(rendered.timing.event_onsets),
                "short_event": rendered.short_event,
                "overflow": rendered.overflow,
                "peak": rendered.peak,
                "pcm_sha256": None if rendered.overflow else rendered.pcm_sha256,
                "file_sha256": None if rendered.overflow else file_sha256(rendered),
            }
        except Exception as exc:
            mismatches.append(_mismatch(item, "render", "a rendered motif", repr(exc)))
            continue
        for key, actual in actual_fields.items():
            if vector.get(key) != actual:
                mismatches.append(_mismatch(item, key, vector.get(key), actual))
    return {"checked": checked, "mismatches": mismatches}


def check_composition_vectors(path: Path) -> JsonObject:
    """Re-render the synthetic books, recompute every composite and every pattern."""
    data = json.loads(path.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    header: dict[str, object] = {"renderer_version": RENDERER_VERSION, "gap_samples": GAP_SAMPLES}
    for key, current in header.items():
        if data.get(key) != current:
            mismatches.append(_mismatch("composition/<header>", key, data.get(key), current))
    checked = 0
    for book in data["books"]:
        book_id, profile = str(book["book_id"]), Profile(book["profile"])
        atoms: dict[str, AtomAudio] = {}
        for row in book["atoms"]:
            checked += 1
            item = f"composition/{book_id}/{row['atom_id']}"
            recipe = Recipe.from_dict(row["recipe"])
            rendered = render(recipe, profile)
            actual_fields: dict[str, object] = {
                "recipe_sha256": recipe.sha256(),
                "n_samples": rendered.n_samples,
                "pcm_sha256": rendered.pcm_sha256,
                "file_sha256": file_sha256(rendered),
            }
            for key, actual in actual_fields.items():
                if row.get(key) != actual:
                    mismatches.append(_mismatch(item, key, row.get(key), actual))
            atoms[row["atom_id"]] = AtomAudio.from_rendered(
                row["atom_id"], rendered, book_id=book_id
            )
        for row in book["messages"]:
            checked += 1
            item = f"composition/{book_id}/{row['message_id']}"
            try:
                action, referent = atoms[row["action_id"]], atoms[row["referent_id"]]
                digest = composite_hash(action, referent)
                actual_fields = {
                    "composite_sha256": digest,
                    "n_samples": message_length(action, referent),
                    "heldout": parse_message_id(row["message_id"]).is_heldout,
                }
                if not row.get("heldout", True):
                    composed = compose_message(action, referent)
                    if composed.pcm_sha256 != digest:
                        mismatches.append(
                            _mismatch(item, "composed_pcm_sha256", digest, composed.pcm_sha256)
                        )
            except Exception as exc:
                mismatches.append(_mismatch(item, "composite", "a composite", repr(exc)))
                continue
            for key, actual in actual_fields.items():
                if row.get(key) != actual:
                    mismatches.append(_mismatch(item, key, row.get(key), actual))
    for row in data["patterns"]:
        checked += 1
        item = f"composition/pattern/{row['name']}"
        parts = {
            role: AtomAudio(atom, Profile.P1, _pattern_pcm(**row[role]))
            for role, atom in (("action", "K-a1"), ("referent", "K-r1"))
        }
        actual_fields = {
            "action_pcm_sha256": parts["action"].pcm_sha256,
            "referent_pcm_sha256": parts["referent"].pcm_sha256,
            "n_samples": message_length(parts["action"], parts["referent"]),
            "composite_sha256": composite_hash(parts["action"], parts["referent"]),
        }
        for key, actual in actual_fields.items():
            if row.get(key) != actual:
                mismatches.append(_mismatch(item, key, row.get(key), actual))
    return {"checked": checked, "mismatches": mismatches}


# ---------------------------------------------------------------------------
# Main loop


def serve(bridge: Bridge, stdin: IO[bytes], stdout: IO[bytes]) -> int:
    """Answer requests until `shutdown` or the end of stdin; return the exit status."""
    while True:
        line = stdin.readline()
        if not line:
            logger.info("end of input; exiting")
            return 0
        try:
            response = bridge.handle_line(line)
        except Exception as exc:  # pragma: no cover - handle_line catches everything
            logger.error("internal error: %s", traceback.format_exc())
            response = failure(None, exc)
        try:
            data = encode(response)
        except (TypeError, ValueError) as exc:
            logger.error("cannot encode the response: %s", exc)
            data = encode(failure(response.get("id"), exc))
        try:
            stdout.write(data)
            stdout.flush()
        except BrokenPipeError:
            logger.info("stdout closed; exiting")
            return 0
        if bridge.closed:
            logger.info("shutdown requested; exiting")
            return 0


_EXIT_SIGNALS: Final[tuple[signal.Signals, ...]] = tuple(
    sig for name in ("SIGTERM", "SIGHUP") if (sig := getattr(signal, name, None)) is not None
)
"""SIGTERM and SIGHUP where the platform has them (Windows has no SIGHUP; the module must
still import there, for example when the sound tests are collected)."""


def _ignore_exit_signals() -> None:
    """Ignore SIGTERM and SIGHUP from now on: the bridge is already on its way out.

    The same signal can arrive twice within milliseconds: a client that stops
    `uv run` may signal uv's whole process group, and uv also forwards the signal to the
    bridge. A second signal must not interrupt the removal of the temp root. A signal
    that is already pending when the handler is replaced runs no handler.
    """
    for signum in _EXIT_SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


def _exit_on_signal(signum: int, frame: FrameType | None) -> None:
    """SIGTERM or SIGHUP: leave through `main`'s `finally`, which removes the temp root.

    The handler first ignores further exit signals, so that no second `SystemExit` can
    cut the cleanup short. `SystemExit` is not an `Exception`, so no request handler
    swallows it. The exit status is 128 + the signal number, as for a process killed by
    that signal.
    """
    _ignore_exit_signals()
    logger.info("signal %d; removing the temp directory and exiting", signum)
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep-temp", action="store_true", help="keep the temp root on exit")
    parser.add_argument("--verbose", action="store_true", help="debug logging on stderr")
    args = parser.parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(name)s: %(levelname)s: %(message)s",
    )
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # nothing but responses may reach the protocol stream
    bridge = Bridge(keep_temp=args.keep_temp)
    for signum in _EXIT_SIGNALS:
        signal.signal(signum, _exit_on_signal)
    logger.info("bridge version %d ready (renderer %s)", BRIDGE_VERSION, RENDERER_VERSION)
    try:
        return serve(bridge, sys.stdin.buffer, protocol_out)
    except KeyboardInterrupt:
        return 130
    finally:
        # No exit signal may interrupt the cleanup, whatever the way out (shutdown, end
        # of input, or a signal). A signal that arrives before the handlers are replaced
        # raises `SystemExit` from `_exit_on_signal`, which ignores further signals
        # first; the inner `finally` still removes the temp root.
        try:
            _ignore_exit_signals()
        finally:
            bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
