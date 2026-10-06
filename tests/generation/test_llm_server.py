"""Pinned-server launcher (#16): refuses to start on any checksum, revision, template,
config or runtime mismatch (fake weights directory), and starts a stand-in server with
the pinned flags, the offline environment and separate startup timing."""

import hashlib
import json
import os
import shutil
import socket
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from av_generation.clock import SystemClock
from av_generation.constants import MODEL_ID, MODEL_REVISION
from av_generation.jsonio import document_text
from av_generation.llm import OpenAICompatibleClient, decoding_schema
from av_generation.llm_manifest import (
    E_CHAT_TEMPLATE,
    E_EXTRA_WEIGHTS,
    E_FILE_BLOB,
    E_MANIFEST,
    E_MISSING,
    E_REVISION,
    E_REVISION_UNKNOWN,
    E_SIZE,
    E_WEIGHTS_SHA256,
    ModelMismatch,
    default_runtime,
    git_blob_sha1_bytes,
    lfs_pointer,
    load_llm_manifest,
    local_revisions,
    manifest_from_hf_api,
    manifest_sha256,
    verify_model_dir,
)
from av_generation.llm_server import (
    E_CONFIG,
    E_CONFIG_MANIFEST,
    E_RUNTIME_MISSING,
    E_RUNTIME_VERSION,
    E_STARTUP,
    OFFLINE_ENV,
    ServerRefused,
    base_url_for,
    config_problems,
    load_server_config,
    main,
    prepare_launch,
    server_config_path,
    server_env,
    start_server,
)
from av_generation.outcomes import LlmStatus
from av_generation.records import LlmRequest, RecordWriter, TimingEvent, read_records
from av_generation.seeds import a3_seed_key

TEMPLATE = "DEMO template {% for m in messages %}{{ m.content }}{% endfor %}"
FILES = {
    "model-00001-of-00002.safetensors": b"DEMO weights shard one " * 64,
    "model-00002-of-00002.safetensors": b"DEMO weights shard two " * 64,
    "model.safetensors.index.json": b'{"weight_map": {}}\n',
    "config.json": b'{"architectures": ["Qwen2ForCausalLM"]}\n',
    "generation_config.json": b'{"temperature": 0.7, "top_p": 0.8}\n',
    "tokenizer.json": b'{"model": "DEMO"}\n',
    "tokenizer_config.json": json.dumps({"chat_template": TEMPLATE}).encode(),
    "vocab.json": b"{}\n",
    "merges.txt": b"#version: DEMO\n",
    "LICENSE": b"DEMO licence text\n",
    "README.md": b"DEMO readme\n",
}
VERSION = "0.30.0"


def _api_info():
    siblings = []
    for path, data in sorted(FILES.items()):
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
            "tokenizer_config": {"chat_template": TEMPLATE},
        },
        "siblings": siblings,
    }


class Snapshot:
    """A fake `hf download --local-dir` snapshot with its own manifest and config."""

    def __init__(self, root: Path):
        self.model_dir = root / "model"
        meta = self.model_dir / ".cache" / "huggingface" / "download"
        meta.mkdir(parents=True)
        for path, data in FILES.items():
            (self.model_dir / path).write_bytes(data)
            (meta / f"{path}.metadata").write_text(
                f"{MODEL_REVISION}\netag-{path}\n1759600000.0\n", encoding="utf-8"
            )
        manifest = manifest_from_hf_api(
            _api_info(), runtime=default_runtime(VERSION), retrieved_utc="2026-10-05"
        )
        self.manifest = root / "manifest.json"
        manifest.write(self.manifest)
        config = load_server_config().to_dict()
        config["manifest_sha256"] = manifest_sha256(self.manifest)
        self.config = root / "server-config.json"
        self.write_config(config)

    def write_config(self, data):
        self.config.write_text(document_text(data), encoding="utf-8", newline="\n")

    def prepare(self, **kwargs):
        options = {
            "config_path": self.config,
            "manifest_file": self.manifest,
            "runtime_version": lambda: VERSION,
            "host": "127.0.0.1",
            "port": 8123,
        }
        options.update(kwargs)
        return prepare_launch(self.model_dir, **options)


@pytest.fixture
def snap(tmp_path):
    return Snapshot(tmp_path)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_committed_config_names_the_committed_manifest():
    config = load_server_config()
    manifest = load_llm_manifest()
    assert config_problems(config, manifest, manifest_file_sha256=manifest_sha256()) == []
    assert config.dtype == "bfloat16" and config.max_model_len >= 16_896
    assert config.served_model_name == MODEL_ID
    raw = server_config_path().read_bytes()
    assert b"\r" not in raw and raw.decode() == document_text(config.to_dict())


def test_fake_snapshot_passes_and_gives_the_pinned_command(snap):
    plan = snap.prepare()
    command = list(plan.command)
    assert command[:3] == ["vllm", "serve", str(snap.model_dir)]
    pairs = dict(zip(command[3::2], command[4::2], strict=True))
    assert pairs == {
        "--served-model-name": MODEL_ID,
        "--dtype": "bfloat16",
        "--max-model-len": "16896",
        "--generation-config": "vllm",
        "--structured-outputs-config": '{"backend":"xgrammar"}',
        "--seed": "0",
        "--load-format": "safetensors",
        "--gpu-memory-utilization": "0.9",
        "--tensor-parallel-size": "1",
        "--host": "127.0.0.1",
        "--port": "8123",
    }
    assert not any("guided" in part for part in command)
    for name, value in OFFLINE_ENV.items():
        assert plan.env[name] == value
    assert plan.base_url == "http://127.0.0.1:8123"
    assert plan.manifest_sha256 == manifest_sha256(snap.manifest)


def _flip_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[3] ^= 0x01
    path.write_bytes(bytes(data))


def _set_revision(snap, revision):
    meta = snap.model_dir / ".cache" / "huggingface" / "download" / "config.json.metadata"
    meta.write_text(f"{revision}\netag\n0\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda s: _flip_byte(s.model_dir / "model-00002-of-00002.safetensors"), E_WEIGHTS_SHA256),
        (lambda s: (s.model_dir / "model-00001-of-00002.safetensors").write_bytes(b"x"), E_SIZE),
        (lambda s: (s.model_dir / "tokenizer.json").unlink(), E_MISSING),
        (lambda s: _set_revision(s, "b" * 40), E_REVISION),
        (lambda s: shutil.rmtree(s.model_dir / ".cache"), E_REVISION_UNKNOWN),
        (lambda s: _flip_byte(s.model_dir / "config.json"), E_FILE_BLOB),
        (lambda s: (s.model_dir / "consolidated.safetensors").write_bytes(b"x"), E_EXTRA_WEIGHTS),
    ],
    ids=["weights", "size", "missing", "revision", "no-revision", "blob", "extra-weights"],
)
def test_model_mismatch_refuses_to_start(snap, tmp_path, mutate, code):
    timing = RecordWriter(tmp_path / "timing.jsonl", types=(TimingEvent,), fsync=False)
    mutate(snap)
    with pytest.raises(ServerRefused) as err:
        snap.prepare(timing=timing, run_id="DEMO-llm-host")
    assert err.value.code == code
    assert "LLM server refused to start" in str(err.value) and code in str(err.value)
    events = read_records(tmp_path / "timing.jsonl", TimingEvent)
    assert [e.event for e in events] == ["startup_start", "startup_end"]
    assert events[1].detail == f"refused {code}" and events[1].component == "llm"


def test_chat_template_must_match(snap):
    manifest = load_llm_manifest(snap.manifest)
    other = replace(manifest, chat_template=replace(manifest.chat_template, sha256="0" * 64))
    with pytest.raises(ModelMismatch) as err:
        verify_model_dir(other, snap.model_dir)
    assert err.value.code == E_CHAT_TEMPLATE
    # a changed template also changes the pinned file itself
    changed = json.dumps({"chat_template": TEMPLATE + " changed"}).encode()
    (snap.model_dir / "tokenizer_config.json").write_bytes(changed)
    with pytest.raises(ServerRefused) as err2:
        snap.prepare()
    assert err2.value.code == E_SIZE


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"manifest_sha256": "0" * 64}, E_CONFIG_MANIFEST),
        ({"max_model_len": 32768}, E_CONFIG),
        ({"dtype": "float16"}, E_CONFIG),
        ({"structured_outputs_backend": "guidance"}, E_CONFIG),
        ({"served_model_name": "DEMO-org/DEMO-model"}, E_CONFIG),
        ({"engine_seed": 7}, E_CONFIG),
    ],
)
def test_config_mismatch_refuses_to_start(snap, change, code):
    data = load_server_config(snap.config).to_dict()
    data.update(change)
    snap.write_config(data)
    with pytest.raises(ServerRefused) as err:
        snap.prepare()
    assert err.value.code == code


def test_runtime_version_and_manifest_must_match(snap):
    with pytest.raises(ServerRefused) as err:
        snap.prepare(runtime_version=lambda: "0.31.0")
    assert err.value.code == E_RUNTIME_VERSION
    with pytest.raises(ServerRefused) as err:
        snap.prepare(runtime_version=lambda: None)
    assert err.value.code == E_RUNTIME_MISSING
    data = json.loads(snap.manifest.read_text(encoding="utf-8"))
    data["model"]["revision"] = "c" * 40
    snap.manifest.write_text(document_text(data), encoding="utf-8", newline="\n")
    with pytest.raises(ServerRefused) as err:
        snap.prepare()
    assert err.value.code == E_MANIFEST


def test_committed_manifest_refuses_a_fake_directory(snap):
    with pytest.raises(ServerRefused) as err:
        prepare_launch(snap.model_dir, runtime_version=lambda: VERSION)
    assert err.value.code in (E_MISSING, E_SIZE)
    assert any("model-00001-of-00004.safetensors" in p for p in err.value.problems)


def test_snapshot_layout_names_the_revision(snap, tmp_path):
    manifest = load_llm_manifest(snap.manifest)
    for revision, ok in ((MODEL_REVISION, True), ("d" * 40, False)):
        target = tmp_path / "hub" / revision / "snapshots" / revision
        shutil.copytree(snap.model_dir, target, ignore=shutil.ignore_patterns(".cache"))
        assert local_revisions(target) == {"snapshot-dir": revision}
        if ok:
            verify_model_dir(manifest, target)
        else:
            with pytest.raises(ModelMismatch, match=E_REVISION):
                verify_model_dir(manifest, target)


def test_base_urls():
    assert base_url_for("0.0.0.0", 8000) == "http://127.0.0.1:8000"
    assert base_url_for("::", 8000) == "http://[::1]:8000"
    assert base_url_for("10.20.0.5", 8000) == "http://10.20.0.5:8000"


@pytest.mark.timeout(180)
def test_start_with_a_stand_in_vllm_and_time_startup(snap, tmp_path):
    timing = RecordWriter(tmp_path / "timing.jsonl", types=(TimingEvent,), fsync=False)
    plan = snap.prepare(
        executable=(sys.executable, "-m", "av_generation.mock_llm"),
        port=free_port(),
        timing=timing,
        run_id="DEMO-llm-host",
    )
    assert plan.env["HF_HUB_OFFLINE"] == "1" and plan.env["VLLM_NO_USAGE_STATS"] == "1"
    with (
        open(tmp_path / "server.log", "wb") as log,
        start_server(plan, startup_timeout_s=120, log_file=log) as server,
    ):
        assert server.startup_ms >= plan.verify_ms
        log_writer = RecordWriter(tmp_path / "llm.jsonl", types=(LlmRequest,), fsync=False)
        client = OpenAICompatibleClient.from_manifest(
            server.base_url,
            plan.manifest,
            run_id="DEMO-llm-host",
            clock=SystemClock(),
            request_log=log_writer,
        )
        key = a3_seed_key("DEMO-A-P01", "K-a1", 1, 1)
        messages = [{"role": "user", "content": "DEMO atom"}]
        assert client.count_prompt_tokens(messages) > 0
        outcome = client.propose(messages, decoding_schema(), key)
        assert outcome.status is LlmStatus.OK
    assert server.process.poll() is not None
    (record,) = read_records(tmp_path / "llm.jsonl", LlmRequest)
    assert record.runtime == f"vllm {VERSION}" and record.model_revision == MODEL_REVISION
    events = read_records(tmp_path / "timing.jsonl", TimingEvent)
    assert [e.event for e in events] == ["startup_start", "startup_end"]
    end = events[1]
    assert end.detail.startswith("ready verify_ms=") and "load_ms=" in end.detail
    assert end.duration_ms == server.startup_ms and end.component == "llm"


@pytest.mark.timeout(120)
def test_a_server_that_dies_during_startup_is_reported(snap, tmp_path):
    timing = RecordWriter(tmp_path / "timing.jsonl", types=(TimingEvent,), fsync=False)
    plan = snap.prepare(
        executable=(sys.executable, "-c", "import sys; sys.exit(3)", "--"),
        port=free_port(),
        timing=timing,
        run_id="DEMO-llm-host",
    )
    with pytest.raises(ServerRefused) as err:
        start_server(plan, startup_timeout_s=60)
    assert err.value.code == E_STARTUP and "code 3" in str(err.value)
    events = read_records(tmp_path / "timing.jsonl", TimingEvent)
    assert events[-1].detail == f"failed {E_STARTUP}"


def test_cli_check_and_refusal(snap, tmp_path, capsys, monkeypatch):
    import av_generation.llm_server as llm_server

    args = ["--model-dir", str(snap.model_dir), "--config", str(snap.config)]
    args += ["--manifest", str(snap.manifest)]
    monkeypatch.setattr(llm_server, "installed_vllm_version", lambda: None)
    assert main(["check", *args]) == 2
    assert "REFUSED E_RUNTIME_MISSING" in capsys.readouterr().err
    monkeypatch.setattr(llm_server, "installed_vllm_version", lambda: VERSION)
    timing = tmp_path / "timing.jsonl"
    assert main(["check", *args, "--timing-log", str(timing), "--run-id", "DEMO-llm-host"]) == 0
    out = capsys.readouterr()
    assert "command: vllm serve" in out.out and "OK verify_ms=" in out.out
    assert "hardware not recorded" in out.err
    events = read_records(timing, TimingEvent)
    assert [e.event for e in events] == ["startup_start", "startup_end"]
    assert events[1].detail == "checked; not started"
    _flip_byte(snap.model_dir / "model-00001-of-00002.safetensors")
    assert main(["serve", *args]) == 2
    err = capsys.readouterr().err
    assert "REFUSED E_WEIGHTS_SHA256" in err and "model-00001-of-00002.safetensors" in err
    assert main(["check", *args, "--timing-log", str(timing)]) == 2
    assert "--run-id" in capsys.readouterr().err


def test_offline_environment_leaves_this_process_alone():
    before = dict(os.environ)
    env = server_env({"PATH": "x"})
    assert env == {"PATH": "x", **OFFLINE_ENV}
    assert dict(os.environ) == before
