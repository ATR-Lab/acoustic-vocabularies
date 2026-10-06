"""The committed LLM manifest (#16): pinned revision, checksums, licence, chat template,
runtime, decoding and decoding-schema hash; mapping to apparatus and freeze fields."""

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from av_generation import freeze
from av_generation.constants import (
    FROZEN_DECODING,
    MAX_INPUT_TOKENS,
    MIN_MAX_MODEL_LEN,
    MODEL_ID,
    MODEL_REVISION,
    SLOT_CAP_MS,
)
from av_generation.genconfig import FallbackPins, PromptHashes, build_generation_config
from av_generation.jsonio import canonical_sha256, document_text, file_set_sha256
from av_generation.llm import decoding_schema_sha256
from av_generation.llm_manifest import (
    E_MANIFEST,
    LlmManifest,
    ModelMismatch,
    apparatus_values,
    chat_template_sha256,
    default_runtime,
    file_kind,
    freeze_values,
    git_blob_sha1_bytes,
    lfs_pointer,
    load_llm_manifest,
    main,
    manifest_errors,
    manifest_from_hf_api,
    manifest_path,
    manifest_sha256,
    probe_hardware,
    prompt_hash,
)

ROOT = Path(__file__).resolve().parents[2]
LLM_DIR = ROOT / "generation" / "llm"
H = "a" * 64

# Hugging Face API metadata of the pinned revision (retrieved 2026-10-05), as recorded.
WEIGHTS = {
    "model-00001-of-00004.safetensors": (
        3945441440,
        "a1333e6293854747c481288ea83b348226af178dd565c49b6f9495ba1966aba7",
        "5e68dcea92e3696ddd4e1d44d9b420a31fe5622c",
    ),
    "model-00002-of-00004.safetensors": (
        3864726352,
        "f5d25a2772cb825164a2a2c0fb6d51a87e282abf21e4dd75bc5cfb3cd0ea6185",
        "e07e23d4595678a717aaad11eef9bc22056c284a",
    ),
    "model-00003-of-00004.safetensors": (
        3864726424,
        "8efdec4c1bc12317ae1a38dc42b595ce777738a64deea3fcb8a0a91381bcdfd5",
        "114c07bb791bac8d2b32109b7729ef2f9a489dfe",
    ),
    "model-00004-of-00004.safetensors": (
        3556377672,
        "1a72d403cdf0c1ec3cb7f289f17b394a01e64394c2e9b3c0f94dbce3faf879bd",
        "f4841c787f178938010ce784a3ec99e5568524b5",
    ),
}


@pytest.fixture(scope="module")
def manifest():
    return load_llm_manifest()


def test_committed_manifest_pins_the_protocol_model(manifest):
    assert manifest_errors(manifest) == ()
    model = manifest.model
    assert (model.id, model.revision) == (MODEL_ID, MODEL_REVISION)
    assert model.tokenizer_revision == MODEL_REVISION
    assert model.license == "apache-2.0" and model.license_file == "LICENSE"
    assert model.architecture == "Qwen2ForCausalLM"
    weights = {
        f.path: (f.size, f.sha256, f.git_blob_sha1) for f in model.files if f.kind == "weights"
    }
    assert weights == WEIGHTS
    assert model.weights_sha256 == file_set_sha256({p: v[1] for p, v in WEIGHTS.items()})
    kinds = {f.path: f.kind for f in model.files}
    for name in ("tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt"):
        assert kinds[name] == "tokenizer"
    assert manifest.provenance.weights_downloaded is False
    assert MODEL_REVISION in manifest.provenance.api_url


def test_committed_manifest_runtime_and_decoding(manifest):
    rt = manifest.runtime
    assert (rt.name, rt.version, rt.precision) == ("vllm", "0.30.0", "bfloat16")
    assert rt.max_model_len >= MIN_MAX_MODEL_LEN == MAX_INPUT_TOKENS + 512
    assert rt.generation_config == "vllm" and rt.structured_outputs_backend == "xgrammar"
    assert rt.structured_outputs_request == "response_format.json_schema"
    d = manifest.decoding
    assert (d.temperature, d.top_p, d.top_k, d.repetition_penalty, d.max_tokens) == (
        FROZEN_DECODING.temperature,
        FROZEN_DECODING.top_p,
        FROZEN_DECODING.top_k,
        FROZEN_DECODING.repetition_penalty,
        FROZEN_DECODING.max_tokens,
    )
    assert (d.max_input_tokens, d.slot_cap_ms) == (MAX_INPUT_TOKENS, SLOT_CAP_MS)
    assert manifest.decoding_schema.sha256 == decoding_schema_sha256()
    assert manifest.decoding_schema.carries_domain is True
    assert manifest.chat_template.inserts_default_system_message is True
    assert manifest.hardware.status == "pending" and manifest.hardware.gpu is None


def test_lfs_pointers_reproduce_the_blob_ids(manifest):
    for path, (size, sha, blob) in WEIGHTS.items():
        pointer = lfs_pointer(sha, size)
        assert len(pointer) == 135, path  # HF reports pointerSize 135
        assert git_blob_sha1_bytes(pointer) == blob
    assert git_blob_sha1_bytes(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_manifest_file_is_canonical_and_schema_valid(manifest):
    raw = manifest_path().read_bytes()
    assert b"\r" not in raw
    assert raw.decode("utf-8") == document_text(manifest.to_dict())
    assert manifest_sha256() == hashlib.sha256(raw).hexdigest()
    for name in ("manifest.schema.json", "server-config.schema.json"):
        data = (LLM_DIR / name).read_bytes()
        assert b"\r" not in data and data.endswith(b"}\n")
        schema = json.loads(data)
        Draft202012Validator.check_schema(schema)
        assert schema["$id"].endswith(f"/generation/llm/{name}")
        assert schema["title"] and schema["description"]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("decoding", "temperature"), 0.8),
        (("model", "revision"), "b" * 40),
        (("model", "weights_sha256"), "0" * 64),
        (("decoding_schema", "sha256"), "0" * 64),
        (("provenance", "weights_downloaded"), True),
        (("runtime", "max_model_len"), 8192),
    ],
)
def test_tampered_manifests_are_refused(manifest, tmp_path, path, value):
    data = copy.deepcopy(manifest.to_dict())
    data[path[0]][path[1]] = value
    target = tmp_path / "manifest.json"
    target.write_text(document_text(data), encoding="utf-8", newline="\n")
    with pytest.raises(ModelMismatch) as err:
        load_llm_manifest(target)
    assert err.value.code == E_MANIFEST
    assert "model does not match the LLM manifest" in str(err.value)


def test_tampered_weight_entry_breaks_its_pointer(manifest):
    files = list(manifest.model.files)
    index = next(i for i, f in enumerate(files) if f.kind == "weights")
    files[index] = replace(files[index], sha256="0" * 64)
    broken = replace(manifest, model=replace(manifest.model, files=tuple(files)))
    errors = manifest_errors(broken)
    assert any("LFS pointer" in e for e in errors)
    assert any("weights_sha256" in e for e in errors)


def test_apparatus_and_freeze_values(manifest):
    values = apparatus_values(manifest)
    assert values == {
        "model_revision": MODEL_REVISION,
        "runtime_precision": "bfloat16",
        "prompt_hash": None,
    }
    assert set(values) <= set(freeze.APPARATUS_FIELDS)
    prompts = PromptHashes(a3_sha256=H, b_sha256="b" * 64)
    assert apparatus_values(manifest, prompts=prompts)["prompt_hash"] == prompt_hash(prompts)
    assert prompt_hash(prompts) == canonical_sha256({"a3_sha256": H, "b_sha256": "b" * 64})
    items = freeze_values(manifest, manifest_file_sha256=manifest_sha256())
    assert set(items) <= set(freeze.REQUIRED_ITEM_KEYS)
    assert items["llm.manifest_sha256"] == manifest_sha256()
    assert items["model.weights_sha256"] == manifest.model.weights_sha256
    assert items["runtime.chat_template_sha256"] == manifest.chat_template.sha256
    assert items["schema.decoding_sha256"] == decoding_schema_sha256()
    assert items["runtime.gpu"] is None and items["runtime.cuda_version"] is None
    assert "xgrammar" in items["decoding.implementation"]


def test_generation_config_takes_the_manifest_hashes():
    config = build_generation_config(
        "DEMO-config-llm",
        llm_manifest_sha256=manifest_sha256(),
        decoding_schema_sha256=decoding_schema_sha256(),
        prompts=PromptHashes(a3_sha256=H, b_sha256=H),
        meanings_sha256=H,
        separation_threshold="0.10",
        fallback=FallbackPins(bank_hash=H, books_sha256={"P1": H, "P2": H, "P3": H}),
    )
    assert config.llm_manifest_sha256 == manifest_sha256()
    assert config.model.revision == MODEL_REVISION


def test_probe_hardware_reads_nvidia_smi():
    outputs = {
        "--query-gpu=name,memory.total,driver_version": "NVIDIA RTX 6000 Ada, 49140, 570.86.15\n",
        "plain": "| NVIDIA-SMI 570.86.15   Driver Version: 570.86.15   CUDA Version: 12.8     |\n",
    }

    def run(command):
        return outputs[command[1]] if len(command) > 1 else outputs["plain"]

    versions = {"torch": "2.9.0", "vllm": "0.30.0"}
    hw = probe_hardware(run=run, version_of=versions.get, now_utc="2026-11-05T09:00:00.000Z")
    assert hw.status == "recorded" and hw.gpu == "NVIDIA RTX 6000 Ada"
    assert hw.vram_mib == 49140 and hw.driver_version == "570.86.15"
    assert hw.cuda_version == "12.8" and hw.vllm_installed == "0.30.0"
    manifest = load_llm_manifest()
    recorded = replace(manifest, hardware=hw)
    assert manifest_errors(recorded) == ()
    assert freeze_values(recorded, manifest_file_sha256=H)["runtime.gpu"].endswith("(49140 MiB)")
    small = replace(recorded, hardware=replace(hw, vram_mib=16000))
    assert any("vram_mib" in e for e in manifest_errors(small)), "needs a >=24 GB GPU"


def _api_info(files, template="DEMO {{ messages }} system"):
    siblings = []
    for path, data in sorted(files.items()):
        entry = {"rfilename": path, "size": len(data)}
        if path.endswith(".safetensors"):
            sha = hashlib.sha256(data).hexdigest()
            entry["blobId"] = git_blob_sha1_bytes(lfs_pointer(sha, len(data)))
            entry["lfs"] = {"sha256": sha, "size": len(data), "pointerSize": 135}
        else:
            entry["blobId"] = git_blob_sha1_bytes(data)
        siblings.append(entry)
    return {
        "id": MODEL_ID,
        "sha": MODEL_REVISION,
        "lastModified": "2025-01-12T02:10:10.000Z",
        "cardData": {"license": "apache-2.0"},
        "config": {
            "architectures": ["Qwen2ForCausalLM"],
            "model_type": "qwen2",
            "tokenizer_config": {"chat_template": template},
        },
        "siblings": siblings,
    }


def test_pin_from_api_metadata(tmp_path, capsys):
    files = {
        "model-00001-of-00001.safetensors": b"DEMO weights",
        "LICENSE": b"DEMO licence text\n",
        "tokenizer_config.json": b"{}",
        "README.md": b"DEMO",
    }
    info = _api_info(files)
    built = manifest_from_hf_api(
        info, runtime=default_runtime("0.30.0"), retrieved_utc="2026-10-05"
    )
    assert manifest_errors(built) == ()
    assert built.chat_template.sha256 == chat_template_sha256("DEMO {{ messages }} system")
    assert built.chat_template.inserts_default_system_message is False
    api = tmp_path / "info.json"
    api.write_text(json.dumps(info), encoding="utf-8")
    out = tmp_path / "manifest.json"
    args = ["pin", "--api-json", str(api), "--vllm-version", "0.30.0", "--retrieved", "2026-10-05"]
    assert main([*args, "--out", str(out)]) == 0
    assert load_llm_manifest(out) == built
    assert "sha256=" in capsys.readouterr().out
    other = dict(info, id="DEMO-org/DEMO-model")
    wrong = manifest_from_hf_api(other, runtime=default_runtime("0.30.0"), retrieved_utc="x")
    with pytest.raises(ModelMismatch, match="differs from constants"):
        wrong.write(tmp_path / "wrong.json")
    with pytest.raises(ValueError, match="chat template"):
        manifest_from_hf_api(
            _api_info(files, template=""), runtime=default_runtime("0.30.0"), retrieved_utc="x"
        )


def test_cli_show_and_verify(tmp_path, capsys):
    assert main(["show"]) == 0
    out = capsys.readouterr().out
    assert f"llm.manifest_sha256 = {manifest_sha256()}" in out
    assert f"apparatus.model_revision = {MODEL_REVISION}" in out
    assert main(["verify", "--model-dir", str(tmp_path / "absent")]) == 2
    assert "REFUSED E_MISSING" in capsys.readouterr().err


@given(st.binary(max_size=2048))
def test_git_blob_matches_the_git_definition(data):
    expected = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    assert git_blob_sha1_bytes(data) == expected


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("model-00001-of-00004.safetensors", "weights"),
        ("model.safetensors.index.json", "weights_index"),
        ("merges.txt", "tokenizer"),
        ("generation_config.json", "config"),
        ("LICENSE", "license"),
        (".gitattributes", "other"),
    ],
)
def test_file_kinds(path, kind):
    assert file_kind(path) == kind


def test_round_trip(manifest):
    assert LlmManifest.from_dict(manifest.to_dict()) == manifest
