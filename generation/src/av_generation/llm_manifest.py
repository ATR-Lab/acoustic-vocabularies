"""The LLM manifest: the pinned model, runtime, chat template and decoding (#16).

`generation/llm/manifest.json` (format `av-generation/llm-manifest`, schema
`generation/llm/manifest.schema.json`) is the authority for everything Study A §3.6
asks to freeze about the model: model and tokenizer revision, per-file checksums and the
weights checksum, licence, chat-template hash, runtime version, precision and structured
outputs implementation, decoding values, decoding-schema hash and (once recorded on the
LLM host) the hardware. Its file SHA-256 is the `llm_manifest_sha256` of the generation
config, the run manifests and the freeze item `llm.manifest_sha256` (#25).

Pinning happens at development time from Hugging Face API metadata only
(`manifest_from_hf_api`; no weights are downloaded): LFS files (the safetensors shards)
carry their LFS SHA-256, every other file its git blob SHA-1 (`blobId`). On the LLM host
`verify_model_dir` checks a downloaded snapshot against the manifest before the server
starts (`llm_server`), and `probe_hardware` records GPU, VRAM, driver and CUDA, and the
vLLM and torch versions of the vLLM executable the server runs.

CLI (development and LLM host):

    python -m av_generation.llm_manifest pin --api-json INFO.json --vllm-version 0.30.0 \\
        --retrieved 2026-10-05 --out generation/llm/manifest.json
    python -m av_generation.llm_manifest verify --model-dir DIR
    python -m av_generation.llm_manifest record-hardware --vllm VLLM   # on the LLM host
    python -m av_generation.llm_manifest show
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal

from av_sound.recipe import strict_json_loads
from jsonschema import Draft202012Validator

from av_generation import constants as C
from av_generation._paths import data_root
from av_generation.clock import utc_text
from av_generation.genconfig import SEED_FUNCTION, PromptHashes
from av_generation.jsonio import (
    CodecError,
    canonical_sha256,
    decode_dataclass,
    file_set_sha256,
    file_sha256,
    read_json,
    to_json_value,
    write_document,
)
from av_generation.llm import DECODING_SCHEMA_SOURCE, decoding_schema_sha256

MANIFEST_FORMAT: Final = "av-generation/llm-manifest"
MANIFEST_VERSION: Final = 1
WIRE_SEED_RULE: Final = "signed-int64-twos-complement"
STRUCTURED_OUTPUTS_REQUEST: Final = "response_format.json_schema"
CHAT_TEMPLATE_FILE: Final = "tokenizer_config.json"
CHAT_TEMPLATE_FIELD: Final = "chat_template"
HF_API_URL: Final = "https://huggingface.co/api/models/{model}/revision/{revision}?blobs=true"

FileKind = Literal["weights", "weights_index", "tokenizer", "config", "license", "other"]
VERIFIED_KINDS: Final[frozenset[str]] = frozenset(
    {"weights", "weights_index", "tokenizer", "config", "license"}
)
"""File kinds checked on the LLM host; `other` files (README, .gitattributes) are listed
for completeness only, so `other` is allowed only for files nothing loads
(`inert_file`)."""
TOKENIZER_FILES: Final[frozenset[str]] = frozenset(
    {
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "tokenizer.model",
        "special_tokens_map.json",
        "added_tokens.json",
        "chat_template.jinja",
        "chat_template.json",
    }
)
"""Files that set the tokenizer or the chat template (verified when listed)."""
CONFIG_FILES: Final[frozenset[str]] = frozenset(
    {"config.json", "generation_config.json", "preprocessor_config.json", "processor_config.json"}
)
WEIGHT_SUFFIXES: Final[tuple[str, ...]] = (".safetensors", ".bin", ".pt", ".pth", ".gguf")
"""Weight-like files: a stray one is refused as `E_EXTRA_WEIGHTS`."""
DOWNLOAD_CACHE_DIR: Final = ".cache"
"""`hf download --local-dir` bookkeeping (revision metadata, locks); never read by vLLM."""
MIN_VRAM_MIB: Final = 22_889
"""Recorded VRAM must be at least 24 GB (24 x 10^9 bytes) in MiB, rounded up; the
manifest schema holds the same number. nvidia-smi reports nominal 24-GB cards below
24 x 1024 MiB (an L4 about 23,034 MiB, an A10 about 23,028 MiB)."""

# Refusal codes (`ModelMismatch.code`)
E_MANIFEST: Final = "E_MANIFEST"
E_MISSING: Final = "E_MISSING"
E_SIZE: Final = "E_SIZE"
E_WEIGHTS_SHA256: Final = "E_WEIGHTS_SHA256"
E_FILE_SHA256: Final = "E_FILE_SHA256"
E_FILE_BLOB: Final = "E_FILE_BLOB"
E_EXTRA_WEIGHTS: Final = "E_EXTRA_WEIGHTS"
E_EXTRA_FILE: Final = "E_EXTRA_FILE"
E_REVISION: Final = "E_REVISION"
E_REVISION_UNKNOWN: Final = "E_REVISION_UNKNOWN"
E_CHAT_TEMPLATE: Final = "E_CHAT_TEMPLATE"


class ModelMismatch(RuntimeError):
    """A model directory or manifest does not match the pins; `.code` names the first
    failed rule and `.problems` lists every problem found."""

    def __init__(self, code: str, problems: Sequence[str]) -> None:
        self.code = code
        self.problems = tuple(problems)
        lines = "\n  - ".join(self.problems)
        super().__init__(f"{code}: model does not match the LLM manifest:\n  - {lines}")


# ---------------------------------------------------------------------------
# Document


@dataclass(frozen=True, slots=True)
class ModelFile:
    path: str
    """POSIX path inside the model snapshot."""
    size: int
    git_blob_sha1: str
    """Hugging Face `blobId`: git blob SHA-1 of the file (of the LFS pointer for LFS files)."""
    sha256: str | None
    """LFS SHA-256 of the content (LFS files); `None` for git-stored files."""
    kind: FileKind


@dataclass(frozen=True, slots=True)
class ModelPin:
    id: str
    revision: str
    tokenizer_revision: str
    license: str
    license_file: str
    architecture: str
    model_type: str
    last_modified_utc: str
    files: tuple[ModelFile, ...]
    weights_sha256: str
    """`jsonio.file_set_sha256({path: sha256})` over the `weights` files."""

    def file(self, path: str) -> ModelFile:
        for entry in self.files:
            if entry.path == path:
                return entry
        raise KeyError(path)


@dataclass(frozen=True, slots=True)
class ChatTemplatePin:
    file: str
    field: str
    sha256: str
    """SHA-256 of the UTF-8 bytes of the template string (`chat_template_sha256`)."""
    inserts_default_system_message: bool
    """The template adds a default system message when the first message is not a system
    message, so prompt tokens are counted by the server (`/tokenize`)."""


@dataclass(frozen=True, slots=True)
class RuntimePin:
    name: Literal["vllm"]
    version: str
    precision: str
    """vLLM `--dtype` (apparatus field `runtime_precision`)."""
    max_model_len: int
    generation_config: Literal["vllm"]
    """`--generation-config vllm`: the model's generation_config.json defaults are not loaded."""
    structured_outputs_backend: str
    structured_outputs_request: str
    engine_seed: int
    load_format: str


@dataclass(frozen=True, slots=True)
class HardwarePin:
    status: Literal["pending", "recorded"]
    gpu: str | None
    vram_mib: int | None
    driver_version: str | None
    cuda_version: str | None
    torch_version: str | None
    vllm_installed: str | None
    recorded_utc: str | None


@dataclass(frozen=True, slots=True)
class DecodingPin:
    temperature: float
    top_p: float
    top_k: int
    repetition_penalty: float
    max_tokens: int
    max_input_tokens: int
    slot_cap_ms: int
    seed_function: str
    wire_seed: str


@dataclass(frozen=True, slots=True)
class DecodingSchemaPin:
    source: str
    sha256: str
    """`jsonio.schema_sha256` of the schema object sent in `response_format`."""
    carries_domain: bool
    """The schema carries the Study A §3.2 enums, not only the structure."""


@dataclass(frozen=True, slots=True)
class Provenance:
    api_url: str
    retrieved_utc: str
    weights_downloaded: bool


@dataclass(frozen=True, slots=True)
class LlmManifest:
    """`generation/llm/manifest.json` (see the module docstring)."""

    model: ModelPin
    chat_template: ChatTemplatePin
    runtime: RuntimePin
    hardware: HardwarePin
    decoding: DecodingPin
    decoding_schema: DecodingSchemaPin
    provenance: Provenance

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        data["format"] = MANIFEST_FORMAT
        data["format_version"] = MANIFEST_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LlmManifest:
        errors = manifest_schema_errors(data)
        if errors:
            raise ModelMismatch(E_MANIFEST, errors)
        body = {k: v for k, v in data.items() if k not in ("format", "format_version")}
        try:
            return decode_dataclass(cls, body)
        except CodecError as err:  # pragma: no cover - the schema catches it first
            raise ModelMismatch(E_MANIFEST, [str(err)]) from err

    def runtime_label(self) -> str:
        """`vllm <version>`: the `runtime` of `LlmRequest` records."""
        return f"{self.runtime.name} {self.runtime.version}"

    def write(self, path: str | os.PathLike[str]) -> str:
        """Check and write the document; returns its file SHA-256."""
        errors = manifest_errors(self)
        if errors:
            raise ModelMismatch(E_MANIFEST, errors)
        return write_document(path, self.to_dict())


def llm_dir() -> Path:
    """`generation/llm/` (manifest, server config and their schemas)."""
    return data_root() / "llm"


def manifest_path() -> Path:
    """The committed `generation/llm/manifest.json`."""
    return llm_dir() / "manifest.json"


@cache
def _schema_validator(name: str) -> Draft202012Validator:
    schema = strict_json_loads((llm_dir() / name).read_bytes())
    return Draft202012Validator(schema)


def llm_schema_errors(name: str, instance: object) -> tuple[str, ...]:
    """Sorted errors of `instance` against `generation/llm/<name>`."""
    errors = []
    for err in _schema_validator(name).iter_errors(instance):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return tuple(sorted(errors))


def manifest_schema_errors(data: object) -> tuple[str, ...]:
    return llm_schema_errors("manifest.schema.json", data)


def load_llm_manifest(path: str | os.PathLike[str] | None = None) -> LlmManifest:
    """Read, schema-check and consistency-check a manifest (default: the committed one)."""
    try:
        data = read_json(path if path is not None else manifest_path())
    except CodecError as err:
        raise ModelMismatch(E_MANIFEST, [str(err)]) from err
    if not isinstance(data, dict):
        raise ModelMismatch(E_MANIFEST, ["the manifest must be a JSON object"])
    manifest = LlmManifest.from_dict(data)
    errors = manifest_errors(manifest)
    if errors:
        raise ModelMismatch(E_MANIFEST, errors)
    return manifest


def manifest_sha256(path: str | os.PathLike[str] | None = None) -> str:
    """File SHA-256 of a manifest (`llm_manifest_sha256` everywhere)."""
    return file_sha256(path if path is not None else manifest_path())


# ---------------------------------------------------------------------------
# Hash helpers


def git_blob_sha1_bytes(data: bytes) -> str:
    """Git blob object ID of `data` (`sha1("blob <len>\\0" + data)`)."""
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def _file_digests(path: Path) -> tuple[str, str]:
    """(git blob SHA-1, SHA-256) of a file, streamed."""
    size = path.stat().st_size
    blob = hashlib.sha1(usedforsecurity=False)
    blob.update(b"blob %d\0" % size)
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            blob.update(chunk)
            sha.update(chunk)
    return blob.hexdigest(), sha.hexdigest()


def lfs_pointer(sha256: str, size: int) -> bytes:
    """The git-LFS pointer file Hugging Face stores for an LFS file."""
    return (
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{sha256}\nsize {size}\n"
    ).encode("ascii")


def chat_template_sha256(template: str) -> str:
    """SHA-256 of the UTF-8 bytes of a chat-template string."""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def weights_sha256(files: Iterable[ModelFile]) -> str:
    """`file_set_sha256` over the weights files (path -> LFS SHA-256)."""
    pairs = {}
    for entry in files:
        if entry.kind == "weights":
            if entry.sha256 is None:
                raise ValueError(f"weights file {entry.path} has no SHA-256")
            pairs[entry.path] = entry.sha256
    if not pairs:
        raise ValueError("no weights files")
    return file_set_sha256(pairs)


def manifest_errors(manifest: LlmManifest) -> tuple[str, ...]:
    """Consistency of a manifest with itself, the code constants and the decoding schema."""
    errors: list[str] = []
    data = manifest.to_dict()
    errors.extend(manifest_schema_errors(data))
    model = manifest.model
    if (model.id, model.revision) != (C.MODEL_ID, C.MODEL_REVISION):
        errors.append(
            f"model {model.id}@{model.revision} differs from constants "
            f"{C.MODEL_ID}@{C.MODEL_REVISION}"
        )
    if model.tokenizer_revision != model.revision:
        errors.append("tokenizer_revision differs from the model revision")
    paths = [f.path for f in model.files]
    if len(set(paths)) != len(paths) or paths != sorted(paths):
        errors.append("files must be unique and sorted by path")
    for entry in model.files:
        if entry.kind == "other" and not inert_file(entry.path):
            errors.append(f"{entry.path}: only inert files (.gitattributes, *.md) may be 'other'")
        if entry.kind == "weights" and entry.sha256 is None:
            errors.append(f"{entry.path}: weights need an LFS SHA-256")
        if entry.sha256 is not None:
            pointer = lfs_pointer(entry.sha256, entry.size)
            if git_blob_sha1_bytes(pointer) != entry.git_blob_sha1:
                errors.append(f"{entry.path}: LFS pointer does not match its blob ID")
    try:
        if weights_sha256(model.files) != model.weights_sha256:
            errors.append("weights_sha256 does not match the weights files")
    except ValueError as err:
        errors.append(str(err))
    if model.license_file not in paths:
        errors.append(f"licence file {model.license_file} is not listed")
    if CHAT_TEMPLATE_FILE not in paths:
        errors.append(f"{CHAT_TEMPLATE_FILE} is not listed")
    d = manifest.decoding
    frozen = C.FROZEN_DECODING
    expected = (
        frozen.temperature,
        frozen.top_p,
        frozen.top_k,
        frozen.repetition_penalty,
        frozen.max_tokens,
        C.MAX_INPUT_TOKENS,
        C.SLOT_CAP_MS,
        SEED_FUNCTION,
        WIRE_SEED_RULE,
    )
    found = (
        d.temperature,
        d.top_p,
        d.top_k,
        d.repetition_penalty,
        d.max_tokens,
        d.max_input_tokens,
        d.slot_cap_ms,
        d.seed_function,
        d.wire_seed,
    )
    if found != expected:
        errors.append(f"decoding {found} differs from the frozen values {expected}")
    if manifest.runtime.max_model_len < C.MIN_MAX_MODEL_LEN:
        errors.append(f"max_model_len must be at least {C.MIN_MAX_MODEL_LEN}")
    if manifest.runtime.structured_outputs_request != STRUCTURED_OUTPUTS_REQUEST:
        errors.append(f"structured outputs must use {STRUCTURED_OUTPUTS_REQUEST}")
    schema_pin = manifest.decoding_schema
    if schema_pin.source != DECODING_SCHEMA_SOURCE:
        errors.append(f"decoding schema source must be {DECODING_SCHEMA_SOURCE}")
    if schema_pin.sha256 != decoding_schema_sha256():
        errors.append(
            f"decoding schema hash {schema_pin.sha256} differs from the current schema "
            f"{decoding_schema_sha256()}"
        )
    if manifest.provenance.weights_downloaded:
        errors.append("weights_downloaded must be false: pins come from API metadata only")
    return tuple(errors)


# ---------------------------------------------------------------------------
# Pinning from Hugging Face API metadata (development time; no download)


def inert_file(path: str) -> bool:
    """A file that neither vLLM nor transformers reads: `.gitattributes` or a Markdown
    document. Only these may be pinned as `other` (listed, not verified)."""
    name = path.rsplit("/", 1)[-1]
    return name == ".gitattributes" or name.endswith(".md")


def file_kind(path: str) -> FileKind:
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".safetensors"):
        return "weights"
    if name == "model.safetensors.index.json":
        return "weights_index"
    if name in TOKENIZER_FILES or path.startswith("additional_chat_templates/"):
        return "tokenizer"
    if name in CONFIG_FILES:
        return "config"
    if name in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
        return "license"
    if inert_file(path):
        return "other"
    return "config"  # unknown files are verified like config files


def manifest_from_hf_api(
    info: Mapping[str, Any],
    *,
    runtime: RuntimePin,
    retrieved_utc: str,
    hardware: HardwarePin | None = None,
) -> LlmManifest:
    """Build a manifest from the Hugging Face model API response for one revision
    (`GET /api/models/<id>/revision/<sha>?blobs=true`; `HF_API_URL`)."""
    model_id = info.get("id")
    revision = info.get("sha")
    if not isinstance(model_id, str) or not isinstance(revision, str):
        raise ValueError("API metadata needs 'id' and 'sha'")
    card = info.get("cardData") or {}
    config = info.get("config") or {}
    tokenizer_config = config.get("tokenizer_config") or {}
    template = tokenizer_config.get(CHAT_TEMPLATE_FIELD)
    if not isinstance(template, str) or not template:
        raise ValueError("API metadata has no chat template")
    files = []
    for sibling in info.get("siblings") or ():
        path = sibling["rfilename"]
        lfs = sibling.get("lfs")
        size = lfs["size"] if lfs else sibling["size"]
        files.append(
            ModelFile(
                path=path,
                size=int(size),
                git_blob_sha1=sibling["blobId"],
                sha256=lfs["sha256"] if lfs else None,
                kind=file_kind(path),
            )
        )
    files.sort(key=lambda f: f.path)
    licenses = [f.path for f in files if f.kind == "license"]
    architectures = config.get("architectures") or [""]
    manifest = LlmManifest(
        model=ModelPin(
            id=model_id,
            revision=revision,
            tokenizer_revision=revision,
            license=str(card.get("license", "")),
            license_file=licenses[0] if licenses else "",
            architecture=str(architectures[0]),
            model_type=str(config.get("model_type", "")),
            last_modified_utc=str(info.get("lastModified", "")),
            files=tuple(files),
            weights_sha256=weights_sha256(files),
        ),
        chat_template=ChatTemplatePin(
            file=CHAT_TEMPLATE_FILE,
            field=CHAT_TEMPLATE_FIELD,
            sha256=chat_template_sha256(template),
            inserts_default_system_message="messages[0]['role'] == 'system'" in template,
        ),
        runtime=runtime,
        hardware=hardware if hardware is not None else pending_hardware(),
        decoding=DecodingPin(
            temperature=C.FROZEN_DECODING.temperature,
            top_p=C.FROZEN_DECODING.top_p,
            top_k=C.FROZEN_DECODING.top_k,
            repetition_penalty=C.FROZEN_DECODING.repetition_penalty,
            max_tokens=C.FROZEN_DECODING.max_tokens,
            max_input_tokens=C.MAX_INPUT_TOKENS,
            slot_cap_ms=C.SLOT_CAP_MS,
            seed_function=SEED_FUNCTION,
            wire_seed=WIRE_SEED_RULE,
        ),
        decoding_schema=DecodingSchemaPin(
            source=DECODING_SCHEMA_SOURCE, sha256=decoding_schema_sha256(), carries_domain=True
        ),
        provenance=Provenance(
            api_url=HF_API_URL.format(model=model_id, revision=revision),
            retrieved_utc=retrieved_utc,
            weights_downloaded=False,
        ),
    )
    return manifest


def default_runtime(version: str) -> RuntimePin:
    """The proposed runtime pin (Study A §3.6; ADR-006 records the decision, #50)."""
    return RuntimePin(
        name="vllm",
        version=version,
        precision="bfloat16",
        max_model_len=C.MIN_MAX_MODEL_LEN,
        generation_config="vllm",
        structured_outputs_backend="xgrammar",
        structured_outputs_request=STRUCTURED_OUTPUTS_REQUEST,
        engine_seed=0,
        load_format="safetensors",
    )


def pending_hardware() -> HardwarePin:
    return HardwarePin("pending", None, None, None, None, None, None, None)


# ---------------------------------------------------------------------------
# Verifying a model directory on the LLM host


def _metadata_revisions(model_dir: Path) -> dict[str, str]:
    """Revision per file from `hf download --local-dir` metadata
    (`.cache/huggingface/download/<file>.metadata`, first line = commit hash)."""
    root = model_dir / ".cache" / "huggingface" / "download"
    found: dict[str, str] = {}
    if not root.is_dir():
        return found
    for meta in sorted(root.rglob("*.metadata")):
        lines = meta.read_text(encoding="utf-8", errors="replace").splitlines()
        if lines:
            rel = meta.relative_to(root).as_posix()[: -len(".metadata")]
            found[rel] = lines[0].strip()
    return found


def local_revisions(model_dir: str | os.PathLike[str]) -> dict[str, str]:
    """Revision evidence of a downloaded snapshot: `{source: revision}`.

    Sources: the Hugging Face cache layout (`.../snapshots/<revision>/`, source
    `snapshot-dir`) and `hf download --local-dir` metadata (one entry per file).
    """
    path = Path(model_dir).resolve()
    found = {f"metadata:{k}": v for k, v in _metadata_revisions(path).items()}
    if path.parent.name == "snapshots":
        found["snapshot-dir"] = path.name
    return found


def verify_model_dir(
    manifest: LlmManifest,
    model_dir: str | os.PathLike[str],
    *,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Refuse a model directory that differs from the manifest (raises `ModelMismatch`).

    Cheap checks first (presence, sizes, stray files, revision evidence, git blob of the
    small files, chat template), then the SHA-256 of every LFS file.

    Every regular file outside `.cache/` must be listed in the manifest. A stray file
    could replace a pinned one at load time: transformers prefers a separate
    `chat_template.jinja` (or `chat_template.json`, `additional_chat_templates/`) over
    the template in `tokenizer_config.json`, and `special_tokens_map.json` or
    `added_tokens.json` change the tokenizer. A stray weight file is refused as
    `E_EXTRA_WEIGHTS`, any other stray file as `E_EXTRA_FILE`.
    """
    root = Path(model_dir)
    if not root.is_dir():
        raise ModelMismatch(E_MISSING, [f"model directory {root} does not exist"])
    problems: list[tuple[str, str]] = []
    listed = {f.path for f in manifest.model.files}
    checked = [f for f in manifest.model.files if f.kind in VERIFIED_KINDS]
    for entry in checked:
        path = root / entry.path
        if not path.is_file():
            problems.append((E_MISSING, f"{entry.path}: missing"))
        elif path.stat().st_size != entry.size:
            problems.append(
                (E_SIZE, f"{entry.path}: {path.stat().st_size} bytes, manifest {entry.size}")
            )
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.split("/", 1)[0] == DOWNLOAD_CACHE_DIR or rel in listed or path.is_dir():
            continue
        if path.name.endswith(WEIGHT_SUFFIXES):
            problems.append((E_EXTRA_WEIGHTS, f"{rel}: weight file not in the manifest"))
        else:
            problems.append(
                (E_EXTRA_FILE, f"{rel}: file not in the manifest (remove it or download again)")
            )
    revisions = local_revisions(root)
    wrong = sorted({v for v in revisions.values() if v != manifest.model.revision})
    if wrong:
        problems.append(
            (E_REVISION, f"revision {', '.join(wrong)} differs from {manifest.model.revision}")
        )
    elif not revisions:
        problems.append(
            (
                E_REVISION_UNKNOWN,
                "no revision evidence (download with `hf download "
                f"{manifest.model.id} --revision {manifest.model.revision} --local-dir DIR`)",
            )
        )
    if problems:
        raise ModelMismatch(problems[0][0], [p for _, p in problems])
    for entry in checked:
        if entry.sha256 is not None:
            continue
        blob, _ = _file_digests(root / entry.path)
        if blob != entry.git_blob_sha1:
            problems.append(
                (E_FILE_BLOB, f"{entry.path}: git blob {blob}, manifest {entry.git_blob_sha1}")
            )
    template_problem = _chat_template_problem(manifest, root)
    if template_problem:
        problems.append((E_CHAT_TEMPLATE, template_problem))
    if problems:
        raise ModelMismatch(problems[0][0], [p for _, p in problems])
    for entry in checked:
        if entry.sha256 is None:
            continue
        if progress is not None:
            progress(f"sha256 {entry.path} ({entry.size} bytes)")
        _, sha = _file_digests(root / entry.path)
        if sha != entry.sha256:
            code = E_WEIGHTS_SHA256 if entry.kind == "weights" else E_FILE_SHA256
            problems.append((code, f"{entry.path}: sha256 {sha}, manifest {entry.sha256}"))
    if problems:
        raise ModelMismatch(problems[0][0], [p for _, p in problems])


def _chat_template_problem(manifest: LlmManifest, root: Path) -> str | None:
    pin = manifest.chat_template
    try:
        data = strict_json_loads((root / pin.file).read_bytes())
    except (OSError, ValueError) as err:
        return f"{pin.file}: unreadable ({err})"
    template = data.get(pin.field) if isinstance(data, dict) else None
    if not isinstance(template, str):
        return f"{pin.file}: no {pin.field} string"
    found = chat_template_sha256(template)
    if found != pin.sha256:
        return f"chat template sha256 {found}, manifest {pin.sha256}"
    return None


# ---------------------------------------------------------------------------
# Mapping to the apparatus manifest and the G4 freeze manifest (#25)


def prompt_hash(prompts: PromptHashes) -> str:
    """Apparatus `prompt_hash`: `canonical_sha256` of `{"a3_sha256", "b_sha256"}` (the
    two prompt-set hashes of #17, as in the generation config)."""
    return canonical_sha256(to_json_value(prompts))


def apparatus_values(
    manifest: LlmManifest, *, prompts: PromptHashes | None = None
) -> dict[str, str | None]:
    """Apparatus-manifest fields filled from the LLM manifest (and #17's prompt hashes):
    `model_revision`, `runtime_precision`, `prompt_hash` (`None` until prompts exist)."""
    return {
        "model_revision": manifest.model.revision,
        "runtime_precision": manifest.runtime.precision,
        "prompt_hash": prompt_hash(prompts) if prompts is not None else None,
    }


def decoding_implementation(manifest: LlmManifest) -> str:
    """Freeze item `decoding.implementation`."""
    rt = manifest.runtime
    return (
        f"{rt.name} {rt.version} {rt.structured_outputs_request} "
        f"backend={rt.structured_outputs_backend} generation_config={rt.generation_config}"
    )


def freeze_values(manifest: LlmManifest, *, manifest_file_sha256: str) -> dict[str, Any]:
    """Values of the freeze items (#25 `freeze.REQUIRED_ITEM_KEYS`) the LLM manifest owns.
    Hardware items are `None` while `hardware.status` is `pending`."""
    hw = manifest.hardware
    return {
        "model.id": manifest.model.id,
        "model.revision": manifest.model.revision,
        "model.tokenizer_revision": manifest.model.tokenizer_revision,
        "model.weights_sha256": manifest.model.weights_sha256,
        "model.license": manifest.model.license,
        "runtime.vllm_version": manifest.runtime.version,
        "runtime.cuda_version": hw.cuda_version,
        "runtime.driver_version": hw.driver_version,
        "runtime.gpu": None if hw.gpu is None else f"{hw.gpu} ({hw.vram_mib} MiB)",
        "runtime.precision": manifest.runtime.precision,
        "runtime.max_model_len": manifest.runtime.max_model_len,
        "runtime.chat_template_sha256": manifest.chat_template.sha256,
        "decoding.implementation": decoding_implementation(manifest),
        "schema.decoding_sha256": manifest.decoding_schema.sha256,
        "llm.manifest_sha256": manifest_file_sha256,
    }


# ---------------------------------------------------------------------------
# Hardware record (LLM host)

Runner = Callable[[Sequence[str]], str]
"""Runs a command and returns its stdout; raises `OSError` or
`subprocess.SubprocessError` when the command cannot run or fails."""

RUNTIME_PROBE_TIMEOUT_S: Final = 300.0
"""Seconds allowed for `vllm --version` or `vllm collect-env` (importing vLLM is slow)."""
_VERSION_RE: Final = re.compile(r"v?(\d+(?:\.\d+)+[0-9A-Za-z.+-]*)")
_TORCH_RE: Final = re.compile(r"^\s*PyTorch version\s*:\s*(\S+)", re.MULTILINE)


def run_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout_s: float = RUNTIME_PROBE_TIMEOUT_S,
) -> str:
    """Run `command` and return its stdout (raises on a failure; see `Runner`)."""
    return subprocess.run(  # noqa: S603 - fixed commands, no shell
        list(command),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=None if env is None else dict(env),
    ).stdout


def parse_runtime_version(output: str) -> str | None:
    """The version printed by `vllm --version`: the last output line that is a bare
    version (log lines printed while vLLM imports are skipped); `None` if there is none."""
    for line in reversed(output.splitlines()):
        match = _VERSION_RE.fullmatch(line.strip())
        if match:
            return match.group(1)
    return None


def runtime_version_of(executable: Sequence[str], *, run: Runner = run_command) -> str | None:
    """The vLLM version of the executable that `vllm serve` would start
    (`<executable> --version`); `None` when it cannot run, fails or prints no version.

    The launcher and `record-hardware` ask the executable, never this interpreter: the
    pinned vLLM lives in its own environment on the LLM host (`generation/docs/llm.md`)."""
    try:
        return parse_runtime_version(run([*executable, "--version"]))
    except (OSError, subprocess.SubprocessError):
        return None


def torch_version_of(executable: Sequence[str], *, run: Runner = run_command) -> str | None:
    """The PyTorch version in the environment of `executable`, from
    `<executable> collect-env` ("PyTorch version: ..."); `None` if not found."""
    try:
        match = _TORCH_RE.search(run([*executable, "collect-env"]))
    except (OSError, subprocess.SubprocessError):
        return None
    return match.group(1) if match else None


def probe_hardware(
    *,
    run: Runner = run_command,
    vllm: Sequence[str] = ("vllm",),
    now_utc: str | None = None,
) -> HardwarePin:
    """GPU name, VRAM, driver and CUDA versions (`nvidia-smi`), and the vLLM and PyTorch
    versions of the vLLM executable `vllm` that the launcher starts (`--version`,
    `collect-env`). Run on the LLM host only; the values go into the manifest's
    `hardware` section."""
    gpu_line = run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
    ).strip()
    first = gpu_line.splitlines()[0] if gpu_line else ""
    name, vram, driver = (part.strip() for part in (first.split(",") + ["", "", ""])[:3])
    cuda = None
    for line in run(["nvidia-smi"]).splitlines():
        if "CUDA Version:" in line:
            cuda = line.split("CUDA Version:", 1)[1].strip().split()[0].strip("|")
            break
    return HardwarePin(
        status="recorded",
        gpu=name or None,
        vram_mib=int(float(vram)) if vram else None,
        driver_version=driver or None,
        cuda_version=cuda,
        torch_version=torch_version_of(vllm, run=run),
        vllm_installed=runtime_version_of(vllm, run=run),
        recorded_utc=now_utc,
    )


# ---------------------------------------------------------------------------
# CLI


def _cmd_pin(args: argparse.Namespace) -> int:
    info = read_json(args.api_json)
    manifest = manifest_from_hf_api(
        info, runtime=default_runtime(args.vllm_version), retrieved_utc=args.retrieved
    )
    digest = manifest.write(args.out)
    print(f"wrote {args.out} sha256={digest}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    manifest = load_llm_manifest(args.manifest)
    try:
        verify_model_dir(manifest, args.model_dir, progress=lambda m: print(m, flush=True))
    except ModelMismatch as err:
        print(f"REFUSED {err}", file=sys.stderr)
        return 2
    print(f"OK {args.model_dir} matches {manifest.model.id}@{manifest.model.revision}")
    return 0


def _cmd_record_hardware(args: argparse.Namespace) -> int:  # pragma: no cover - LLM host
    from datetime import UTC, datetime

    from av_generation.llm_server import server_env  # the launcher's offline environment

    path = Path(args.manifest) if args.manifest else manifest_path()
    manifest = load_llm_manifest(path)
    hardware = probe_hardware(
        run=lambda command: run_command(command, env=server_env()),
        vllm=(args.vllm,),
        now_utc=utc_text(datetime.now(UTC)),
    )
    if hardware.vllm_installed != manifest.runtime.version:
        print(
            f"REFUSED `{args.vllm} --version` reports vLLM {hardware.vllm_installed}; "
            f"pinned {manifest.runtime.version}",
            file=sys.stderr,
        )
        return 2
    digest = replace(manifest, hardware=hardware).write(path)
    print(f"recorded hardware in {path} sha256={digest}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    path = Path(args.manifest) if args.manifest else manifest_path()
    manifest = load_llm_manifest(path)
    digest = manifest_sha256(path)
    for key, value in sorted(freeze_values(manifest, manifest_file_sha256=digest).items()):
        print(f"{key} = {value}")
    for key, value in apparatus_values(manifest).items():
        print(f"apparatus.{key} = {value}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m av_generation.llm_manifest")
    sub = parser.add_subparsers(dest="command", required=True)
    pin = sub.add_parser("pin", help="build a manifest from saved Hugging Face API JSON")
    pin.add_argument("--api-json", required=True)
    pin.add_argument("--vllm-version", required=True)
    pin.add_argument("--retrieved", required=True, help="retrieval date (UTC), e.g. 2026-10-05")
    pin.add_argument("--out", required=True)
    pin.set_defaults(func=_cmd_pin)
    verify = sub.add_parser("verify", help="check a downloaded model directory")
    verify.add_argument("--model-dir", required=True)
    verify.add_argument("--manifest", default=None)
    verify.set_defaults(func=_cmd_verify)
    record = sub.add_parser("record-hardware", help="record GPU/driver/CUDA (LLM host)")
    record.add_argument("--manifest", default=None)
    record.add_argument(
        "--vllm", default="vllm", help="the vLLM executable the server runs (default: on PATH)"
    )
    record.set_defaults(func=_cmd_record_hardware)
    show = sub.add_parser("show", help="print the freeze and apparatus values")
    show.add_argument("--manifest", default=None)
    show.set_defaults(func=_cmd_show)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
