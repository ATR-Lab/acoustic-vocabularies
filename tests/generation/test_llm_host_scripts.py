"""GPU-host bring-up (#16): the scripts in `generation/llm/host/`, their pins and
`av_generation.llm_host`.

- the scripts carry no pin of their own: model ID, revision and vLLM version come from
  `generation/llm/manifest.json` and `server-config.json` (`llm_host pins`), and a dry run
  prints commands with exactly those values;
- `bash -n` parses both scripts, and no script or unit file holds an IP address, a host
  name or an absolute home path (placeholders only, docs/data-policy.md);
- the freeze commands fill exactly the draft's GPU-host items, with the values
  `freeze build` compares with the committed manifest;
- the outbound check and the acceptance report, on synthetic `ss` output and a mock
  benchmark run.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from av_generation import constants as C
from av_generation import llm_host
from av_generation.jsonio import document_text, file_sha256, read_json
from av_generation.llm_bench import run_cli
from av_generation.llm_manifest import MIN_VRAM_MIB, freeze_values, load_llm_manifest
from av_generation.llm_server import load_server_config

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "generation" / "llm" / "host"
SCRIPTS = (HOST / "bringup.sh", HOST / "evidence.sh")
UNIT = HOST / "av-llm.service.example"
DRAFT = ROOT / "generation" / "FREEZE-v1.0.draft.json"
BASH = shutil.which("bash")
POSIX_BASH = pytest.mark.skipif(
    sys.platform == "win32" or BASH is None, reason="the host scripts are bash on Linux"
)


def _dry_run(script, *args):
    env = {"AV_PY": sys.executable, "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [BASH, str(script), "--dry-run", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_pins_come_from_the_repository_files():
    manifest = load_llm_manifest()
    config = load_server_config()
    pins = llm_host.pins()
    assert pins["MODEL_ID"] == C.MODEL_ID == manifest.model.id == config.served_model_name
    assert pins["MODEL_REVISION"] == C.MODEL_REVISION == manifest.model.revision
    assert pins["VLLM_VERSION"] == manifest.runtime.version
    assert pins["DEFAULT_PORT"] == str(config.port)
    assert pins["MAX_MODEL_LEN"] == str(manifest.runtime.max_model_len) == "16896"
    assert pins["PRECISION"] == "bfloat16" and pins["MIN_VRAM_MIB"] == str(MIN_VRAM_MIB)
    shards = [f for f in manifest.model.files if f.kind == "weights"]
    assert int(pins["WEIGHTS_BYTES"]) == sum(f.size for f in shards) == 15_231_271_888
    assert int(pins["SMALLEST_SHARD_BYTES"]) == min(f.size for f in shards)
    text = llm_host.shell_assignments(pins)
    assert f"MODEL_REVISION={C.MODEL_REVISION}\n" in text and "HARDWARE_LABEL=''\n" in text


def test_scripts_hold_no_pins_addresses_or_home_paths():
    manifest = load_llm_manifest()
    for path in (*SCRIPTS, UNIT):
        text = path.read_text(encoding="utf-8")
        assert C.MODEL_REVISION not in text and not re.search(r"\b[0-9a-f]{40}\b", text), path
        assert manifest.runtime.version not in text, path
        assert C.MODEL_ID not in text, path
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text), path
        assert not re.search(r"\[?[0-9a-f]{0,4}::[0-9a-f:]*\]?:\d", text), path
        assert "localhost" not in text.lower(), path
        assert not re.search(r"/(home|Users|root)/|~/|\$HOME", text), path
        assert not re.search(r"\b[\w-]+\.(com|org|net|edu|io|lan|local|internal)\b", text), path
    unit = UNIT.read_text(encoding="utf-8")
    for placeholder in ("<checkout>", "<model-dir>", "<vllm-env>", "<station-net address>"):
        assert placeholder in unit


@POSIX_BASH
def test_scripts_parse():
    for script in SCRIPTS:
        result = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert script.read_text(encoding="utf-8").splitlines()[:1] == ["#!/usr/bin/env bash"]
        assert "set -euo pipefail" in script.read_text(encoding="utf-8")


@POSIX_BASH
def test_bringup_dry_run_uses_the_pinned_values(tmp_path):
    manifest = load_llm_manifest()
    env, model = tmp_path / "vllm-env", tmp_path / "model"
    out = _dry_run(SCRIPTS[0], "--vllm-env", str(env), "--model-dir", str(model))
    assert "+ uv sync --project generation --locked" in out
    assert f"pip install vllm=={manifest.runtime.version}\n" in out
    assert f"hf download {C.MODEL_ID} --revision {C.MODEL_REVISION} --local-dir {model}\n" in out
    assert f"llm_manifest verify --model-dir {model}" in out
    assert f"llm_manifest record-hardware --vllm {env}/bin/vllm" in out  # hardware pending
    assert "llm_host update-config" in out
    port = load_server_config().port
    assert f"llm_server check --model-dir {model} --vllm {env}/bin/vllm" in out
    assert f"--port {port}" in out and "llm_server serve" in out
    assert f">= {MIN_VRAM_MIB} MiB" in out and "dry run" in out
    lines = out.splitlines()
    commands = [line for line in lines if line.startswith("+ ")]
    download = next(i for i, line in enumerate(commands) if "/bin/hf download " in line)
    after = commands[download + 1 :]  # nothing after the download uses the network
    assert after and all("download" not in c and "pip install" not in c for c in after)
    skipped = _dry_run(
        SCRIPTS[0], "--vllm-env", str(env), "--model-dir", str(model), "--skip-install",
        "--skip-download", "--host", "station-address", "--port", "9000",
    )  # fmt: skip
    assert "pip install" not in skipped and "/bin/hf download" not in skipped
    assert "--host station-address --port 9000" in skipped


@POSIX_BASH
def test_bringup_needs_its_directories():
    result = subprocess.run(
        [BASH, str(SCRIPTS[0]), "--dry-run"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 2 and "--vllm-env and --model-dir are required" in result.stderr
    result = subprocess.run(
        [BASH, str(SCRIPTS[0]), "--bogus"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 2 and "unknown argument" in result.stderr
    result = subprocess.run([BASH, str(SCRIPTS[0]), "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "--vllm-env <vllm-env>" in result.stdout


@POSIX_BASH
def test_evidence_dry_run(tmp_path):
    out = _dry_run(
        SCRIPTS[1], "--out", str(tmp_path / "evidence"), "--allow", "192.0.2.0/24",
        "--environment", "TEST label", "--model-dir", str(tmp_path / "model"),
        "--vllm-env", str(tmp_path / "env"), "--calls", "300", "--repeat", "50",
    )  # fmt: skip
    port = load_server_config().port
    assert f"llm_bench --base-url http://{load_server_config().host}:{port}" in out
    assert "--calls 300 --repeat 50" in out
    assert out.count("llm_server check --model-dir") == 2
    assert "test_llm_client.py -k slow" in out and "test_seed.py" in out
    assert f"llm_host report --out-dir {tmp_path / 'evidence'}" in out
    assert not (tmp_path / "evidence").exists()  # a dry run writes nothing
    # without the hardware record and no --environment it stops
    result = subprocess.run(
        [BASH, str(SCRIPTS[1]), "--dry-run", "--out", str(tmp_path / "e2")],
        capture_output=True,
        text=True,
        env={"AV_PY": sys.executable, "PATH": "/usr/bin:/bin"},
        timeout=60,
    )
    assert result.returncode == 2 and "run bringup.sh first" in result.stderr


@POSIX_BASH
def test_evidence_refuses_an_output_inside_a_git_work_tree(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    result = subprocess.run(
        [BASH, str(SCRIPTS[1]), "--dry-run", "--out", str(repo / "evidence")],
        capture_output=True,
        text=True,
        env={"AV_PY": sys.executable, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        timeout=60,
    )
    assert result.returncode == 2 and "inside a git work tree" in result.stderr


def _recorded_manifest(tmp_path):
    manifest = load_llm_manifest()
    hardware = replace(
        manifest.hardware,
        status="recorded",
        gpu="DEMO GPU",
        vram_mib=23_034,
        driver_version="570.00",
        cuda_version="12.8",
        torch_version="2.9.0",
        vllm_installed=manifest.runtime.version,
        recorded_utc="2026-12-14T09:00:00.000Z",
    )
    recorded = replace(manifest, hardware=hardware)
    path = tmp_path / "manifest.json"
    recorded.write(path)
    return recorded, path


def test_freeze_commands_fill_the_drafts_gpu_host_items(tmp_path):
    draft = read_json(DRAFT)
    pending = {
        i["key"] for i in draft["items"] if str(i["source"]).startswith("PENDING (GPU host)")
    }
    assert set(llm_host.GPU_HOST_ITEMS) == pending and len(pending) == 8
    with pytest.raises(llm_host.HostError, match="record the hardware"):
        llm_host.freeze_commands(load_llm_manifest(), manifest_file_sha256="x", draft="d.json")
    manifest, path = _recorded_manifest(tmp_path)
    digest = file_sha256(path)
    lines = llm_host.freeze_commands(manifest, manifest_file_sha256=digest, draft="<draft>")
    values = freeze_values(manifest, manifest_file_sha256=digest)
    assert len(lines) == 8
    for key, line in zip(llm_host.GPU_HOST_ITEMS, lines, strict=True):
        assert line.startswith(f"python -m av_generation.freeze record {key} ")
        assert str(values[key]) in line and line.endswith("--manifest '<draft>'")
    assert "--value 16896" in lines[5] and "--text 12.8" in lines[1]
    assert "--text 'DEMO GPU (23034 MiB)'" in lines[3]
    assert llm_host.hardware_label(manifest).startswith("DEMO GPU 23034 MiB, driver 570.00")
    assert llm_host.main(["freeze-commands", "--draft", "d", "--manifest", str(path)]) == 0
    assert llm_host.main(["freeze-commands", "--draft", "d"]) == 2  # hardware pending


def test_update_config_names_the_manifest_hash(tmp_path, capsys):
    manifest, manifest_path = _recorded_manifest(tmp_path)
    config = tmp_path / "server-config.json"
    shutil.copy(ROOT / "generation" / "llm" / "server-config.json", config)
    old, new = llm_host.update_server_config(config, manifest_path)
    assert new == file_sha256(manifest_path) != old
    assert load_server_config(config).manifest_sha256 == new
    assert config.read_text(encoding="utf-8") == document_text(read_json(config))
    assert llm_host.main(["update-config", "--config", str(config), "--manifest",
                          str(manifest_path)]) == 0  # fmt: skip
    assert "already names" in capsys.readouterr().out
    (tmp_path / "bad.json").write_text("[]", encoding="utf-8")
    with pytest.raises(llm_host.HostError):
        llm_host.update_server_config(tmp_path / "bad.json", manifest_path)


# Addresses from the documentation ranges (RFC 5737, RFC 3849) and loopback only.
LISTENING = """\
LISTEN 0      4096   0.0.0.0:22       0.0.0.0:*
LISTEN 0      4096   192.0.2.10:8000  0.0.0.0:*
LISTEN 0      4096   [::]:22          [::]:*
"""
ESTABLISHED = """\
# 2026-12-14T10:00:00Z
0      0      192.0.2.10:22        198.51.100.7:51234
0      0      192.0.2.10:8000      192.0.2.20:40000
0      0      127.0.0.1:41000      127.0.0.1:8000
0      0      [::ffff:127.0.0.1]:41001 [::ffff:127.0.0.1]:8000
0      0      192.0.2.10:50000     192.0.2.30:443
0      0      192.0.2.10:50001     203.0.113.5:443
0      0      [2001:db8::1]:50002  [2001:db8:1::9]:443  users:(("python",pid=7,fd=3))
not an ss line
"""


def test_outbound_connections_are_found_from_ss_samples(tmp_path, capsys):
    assert llm_host.listening_ports(LISTENING) == {22, 8000}
    found = llm_host.outbound_connections(ESTABLISHED, LISTENING, ["192.0.2.0/24"])
    assert len(found) == 2  # inbound (22, 8000), loopback and the station network are fine
    assert any("203.0.113.5:443" in line for line in found)
    assert any("2001:db8:1::9" in line for line in found)
    assert llm_host.outbound_connections(ESTABLISHED, LISTENING, ["0.0.0.0/0", "::/0"]) == []
    est, lis = tmp_path / "est.txt", tmp_path / "lis.txt"
    est.write_text(ESTABLISHED, encoding="utf-8")
    lis.write_text(LISTENING, encoding="utf-8")
    assert llm_host.main(["outbound", "--established", str(est), "--listening", str(lis),
                          "--allow", "192.0.2.0/24"]) == 1  # fmt: skip
    assert "OUTBOUND" in capsys.readouterr().out
    assert llm_host.main(["outbound", "--established", str(est), "--listening", str(lis),
                          "--allow", "0.0.0.0/0", "--allow", "::/0"]) == 0  # fmt: skip


def _evidence(tmp_path, *, calls=3, repeat=2):
    out = tmp_path / "evidence"
    out.mkdir()
    args = argparse.Namespace(
        mock=True, base_url=None, out_dir=str(out / "bench"), calls=calls, repeat=repeat,
        environment=None, run_id="DEMO-llm-host-test", manifest=None, request_log=None,
        messages=[],
    )  # fmt: skip
    run_cli(args)
    for name, code, log in (
        ("refusal-revision", 2, "REFUSED E_REVISION: ..."),
        ("refusal-weights", 2, "REFUSED E_WEIGHTS_SHA256: ..."),
        ("slow-cap", 0, "1 passed"),
        ("seed", 0, "8 passed"),
    ):
        (out / f"{name}.exit").write_text(f"{code}\n", encoding="utf-8")
        (out / f"{name}.log").write_text(log, encoding="utf-8")
    (out / "ss-listening.txt").write_text(LISTENING, encoding="utf-8")
    (out / "ss-established.txt").write_text(ESTABLISHED.splitlines()[1] + "\n", encoding="utf-8")
    (out / "allowed.txt").write_text("192.0.2.0/24\n", encoding="utf-8")
    return out


def test_report_on_a_mock_run_is_not_gpu_evidence(tmp_path):
    out = _evidence(tmp_path)
    criteria = {c.text: c for c in llm_host.report(out)}
    assert criteria[llm_host.C_REFUSAL].status == "PASS"
    assert criteria[llm_host.C_LOGGED].status == "FAIL"
    assert "not GPU evidence" in criteria[llm_host.C_LOGGED].evidence
    assert criteria[llm_host.C_P95].status == "FAIL"
    assert criteria[llm_host.C_CAP].status == criteria[llm_host.C_SEED].status == "PASS"
    assert criteria[llm_host.C_OUTBOUND].status == "PASS"
    assert criteria[llm_host.C_HARDWARE].status == "PENDING"
    assert criteria[llm_host.C_REPEAT].status == "PASS"
    assert llm_host.main(["report", "--out-dir", str(out)]) == 1
    assert (out / "acceptance.md").read_text(encoding="utf-8").startswith("# #16 acceptance")


def test_report_passes_a_complete_gpu_run(tmp_path):
    out = _evidence(tmp_path, calls=2, repeat=1)
    path = out / "bench" / "llm_latency_summary.json"
    summary = read_json(path)
    summary["environment"] = "DEMO GPU 23034 MiB, driver 570.00, CUDA 12.8, vllm 0.30.0"
    for profile in summary["profiles"].values():
        profile.update(calls=300, frozen_values_and_seed_logged=300, distinct_seeds=300)
    path.write_text(json.dumps(summary), encoding="utf-8")
    manifest, _ = _recorded_manifest(tmp_path)
    criteria = llm_host.report(out, manifest=manifest)
    assert [c.status for c in criteria] == ["PASS"] * 8, llm_host.report_text(criteria)
    # an outbound connection, a slow worst case and a missing refusal each fail or pend
    (out / "ss-established.txt").write_text(ESTABLISHED, encoding="utf-8")
    summary["profiles"]["worst_case"]["latency_ms"]["p95"] = C.SLOT_CAP_MS
    summary["profiles"]["worst_case"]["p95_under_slot_cap"] = False
    path.write_text(json.dumps(summary), encoding="utf-8")
    (out / "refusal-weights.exit").unlink()
    (out / "refusal-revision.log").write_text("OK", encoding="utf-8")
    statuses = {c.text: c.status for c in llm_host.report(out, manifest=manifest)}
    assert statuses[llm_host.C_OUTBOUND] == statuses[llm_host.C_P95] == "FAIL"
    assert statuses[llm_host.C_REFUSAL] == "FAIL"
    small = replace(manifest, hardware=replace(manifest.hardware, vram_mib=16_376))
    assert llm_host.report(out, manifest=small)[6].status == "FAIL"


def test_report_before_any_evidence_is_pending(tmp_path):
    statuses = [c.status for c in llm_host.report(tmp_path)]
    assert statuses == ["PENDING"] * 8


def test_the_runbook_states_the_memory_facts():
    text = (ROOT / "generation" / "docs" / "llm.md").read_text(encoding="utf-8")
    assert "15,231,271,888 bytes" in text and "about 15.2 GB" in text
    assert f"{MIN_VRAM_MIB:,} MiB" in text and "16 GB" in text
    assert "generation/llm/host/bringup.sh" in text and "generation/llm/host/evidence.sh" in text
    for key in llm_host.GPU_HOST_ITEMS:
        assert f"`{key}`" in text
