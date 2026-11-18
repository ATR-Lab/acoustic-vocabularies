"""G4 freeze assembly (#308): fill a restricted copy of the draft, refuse bad evidence,
print the sign-off status, write the vLLM lock.

Every input here is DEMO: an LLM manifest with a DEMO hardware record, an evidence
directory in the layout of `evidence.sh` (#16) written by the test, a fake vLLM lock and a
DEMO inputs file. Nothing is a G4 value, a GPU run or a sign-off.
"""

import dataclasses
import hashlib
import re
import shutil
from pathlib import Path

import pytest

from av_generation import freeze, freeze_assembly
from av_generation import genconfig as gc
from av_generation import llm_manifest as lm
from av_generation.jsonio import document_text, read_json
from av_generation.meanings import load_meanings

ROOT = Path(__file__).resolve().parents[2]
DEMO_MEANINGS = ROOT / "generation/examples/demo-meanings"
ISSUE = "https://github.com/ATR-Lab/acoustic-vocabularies/issues/25"
VLLM = lm.load_llm_manifest().runtime.version
TORCH = "2.9.0"
PACKAGES = (
    f"vllm=={VLLM}",
    f"torch=={TORCH}",
    "xgrammar==0.1.25",
    "transformers==4.57.1",
    "pip==25.2",
    "setuptools==80.9.0",
)
"""A fake `pip freeze --all` of the vLLM environment (test only)."""
ROLES = {"GPU host", "threshold check", "pilot panels", "owner at G4"}
CRITERIA = {
    "refusal": "Server start fails with a clear error when the weights checksum or revision "
    "differs from the manifest",
    "logged": "300 of 300 benchmark calls log temperature=0.7, top_p=0.9, top_k=50, "
    "repetition_penalty=1.0, max_tokens=512 and a derived seed",
    "cap": "A call exceeding 40 s returns timeout within 40.5 s (slowed-server test)",
    "seed": "The seed function gives identical output for 10,000 sample keys, with 0 "
    "collisions across the A and B key spaces",
    "p95": "p95 slot latency at the worst-case prompt size is under 40 s on the LLM GPU",
    "outbound": "No outbound network connection occurs during a session",
    "hardware": "Checklist: vLLM, CUDA and driver versions pinned; GPU and VRAM recorded",
    "repeat": "Checklist: same-seed repeat test run; byte-identical outputs recorded",
}
"""The #16 acceptance criteria in the wording of `llm_host.report` (#301)."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _demo_manifest(**hardware) -> lm.LlmManifest:
    manifest = lm.load_llm_manifest()
    recorded = dataclasses.replace(
        manifest.hardware,
        status="recorded",
        gpu="DEMO GPU",
        vram_mib=23_034,
        driver_version="570.00",
        cuda_version="12.8",
        torch_version=f"{TORCH}+cu128",
        vllm_installed=manifest.runtime.version,
        recorded_utc="DEMO-not-a-record",
    )
    return dataclasses.replace(manifest, hardware=dataclasses.replace(recorded, **hardware))


def _label(m: lm.LlmManifest) -> str:
    hw = m.hardware
    return (
        f"{hw.gpu} {hw.vram_mib} MiB, driver {hw.driver_version}, CUDA {hw.cuda_version}, "
        f"{m.runtime_label()}, torch {hw.torch_version}"
    )


def _acceptance(m: lm.LlmManifest, statuses: dict[str, str] | None = None) -> str:
    hw = m.hardware
    notes = {
        "refusal": "refusal-revision: exit 2, E_REVISION in the log; refusal-weights: exit 2, "
        "E_WEIGHTS_SHA256 in the log",
        "logged": "realistic: 300 of 300 logged, 300 distinct seeds; worst_case: 300 of 300 "
        "logged, 300 distinct seeds",
        "cap": "slow-cap: exit 0",
        "seed": "seed: exit 0 (macOS, Linux and Windows: CI)",
        "p95": "p95 12000 ms at 16373 prompt tokens",
        "outbound": "0 connections opened to peers outside loopback and the station network",
        "hardware": f"hardware recorded: {hw.gpu} {hw.vram_mib} MiB (minimum {lm.MIN_VRAM_MIB}), "
        f"driver {hw.driver_version}, CUDA {hw.cuda_version}, vLLM {hw.vllm_installed}, "
        f"torch {hw.torch_version}",
        "repeat": "50 of 50 keys byte-identical",
    }
    rows = [
        f"| {(statuses or {}).get(name, 'PASS')} | {text} | {notes[name]} |"
        for name, text in CRITERIA.items()
    ]
    head = ["# #16 acceptance on the LLM host", "", "| Status | Criterion | Evidence |"]
    return "\n".join([*head, "| --- | --- | --- |", *rows]) + "\n"


def _write_json(path: Path, doc: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document_text(doc), encoding="utf-8", newline="\n")
    return path


def _inputs_doc() -> dict:
    return {
        "format": freeze_assembly.INPUTS_FORMAT,
        "format_version": 1,
        "items": {
            "separation.evidence_sha256": {
                "value": _sha("DEMO separation summary"),
                "source": "DEMO: SHA-256 of a synthetic separation summary (test only)",
            },
            "pilot.timing_review": {
                "value": {
                    "panel_sessions": 6,
                    "pilot_books": 3,
                    "max_atom_minutes": 18.5,
                    "within_budget": True,
                    "reference": ISSUE + "#issuecomment-0",
                },
                "source": "DEMO timing review (test only)",
            },
            "pilot.audit_sha256": {
                "value": {"DEMO-A-pilot": _sha("DEMO audit tables")},
                "source": "DEMO audit tables (test only)",
            },
            "meanings.sha256": {
                "meaning_set": str(DEMO_MEANINGS),
                "source": "DEMO meaning set (test only)",
            },
        },
    }


@dataclasses.dataclass
class Kit:
    """DEMO inputs of one fill, in a restricted folder outside any git work tree."""

    draft: Path
    manifest: Path
    evidence: Path
    lock: Path
    inputs: Path
    demo: lm.LlmManifest

    def gpu_args(self) -> list[str]:
        return [
            "--llm-manifest",
            str(self.manifest),
            "--evidence",
            str(self.evidence),
            "--vllm-lock",
            str(self.lock),
        ]

    def args(self) -> list[str]:
        return ["fill", "--draft", str(self.draft), *self.gpu_args(), "--inputs", str(self.inputs)]


@pytest.fixture(scope="module")
def built_draft():
    """A draft built from the repository (`freeze.draft_manifest`)."""
    return freeze.draft_manifest()


@pytest.fixture
def kit(tmp_path, built_draft) -> Kit:
    base = tmp_path / "restricted"
    demo = _demo_manifest()
    manifest = base / "llm-manifest.json"
    base.mkdir()
    demo.write(manifest)
    evidence = base / "llm-evidence"
    (evidence / "bench").mkdir(parents=True)
    (evidence / "acceptance.md").write_text(_acceptance(demo), encoding="utf-8")
    for name in freeze_assembly.REFUSALS:
        (evidence / f"{name}.exit").write_text("2\n", encoding="utf-8")
        (evidence / f"{name}.log").write_text("REFUSED E_DEMO\n", encoding="utf-8")
    _write_json(
        evidence / freeze_assembly.SUMMARY,
        {
            "environment": _label(demo),
            "outbound_refused": [],
            "profiles": {},
            "run_id": "DEMO-llm-bench-gpu-1",
            "runtime": demo.runtime_label(),
            "slot_cap_ms": 40000,
        },
    )
    lock = base / "vllm-lock.txt"
    lock.write_text(
        freeze_assembly.lock_text("\n".join(reversed(PACKAGES)), "12.8"), encoding="utf-8"
    )
    inputs = _write_json(base / "g4-inputs.json", _inputs_doc())
    draft = _write_json(base / "freeze-draft.json", built_draft)
    return Kit(draft, manifest, evidence, lock, inputs, demo)


def _fill(kit: Kit, *extra: str) -> int:
    return freeze_assembly.main([*kit.args(), *extra])


def _refused(kit: Kit, capsys, code: str, *extra: str, args: list[str] | None = None) -> str:
    before = kit.draft.read_bytes()
    assert freeze_assembly.main(args or [*kit.args(), *extra]) == 2
    err = capsys.readouterr().err
    assert f"error: {code}" in err, err
    assert kit.draft.read_bytes() == before
    assert not list(kit.draft.parent.glob(".*.tmp"))
    return err


# ---------------------------------------------------------------------------
# Filling


def test_fill_records_every_gpu_host_and_input_item(kit, capsys):
    assert _fill(kit) == 0
    out = capsys.readouterr().out
    filled = read_json(kit.draft)
    values = freeze.item_values(filled)
    sources = {i["key"]: i["source"] for i in filled["items"]}
    expected = lm.freeze_values(kit.demo, manifest_file_sha256=lm.manifest_sha256(kit.manifest))
    for key in freeze_assembly.MANIFEST_ITEMS:
        assert values[key] == expected[key], key
        assert sources[key].startswith("GPU host: ") and "file SHA-256" in sources[key], key
    assert values["runtime.gpu"] == "DEMO GPU (23034 MiB)"
    assert values["runtime.cuda_version"] == "12.8"
    cuda = sources["runtime.cuda_version"]
    assert "nvidia-smi" in cuda and "torch.version.cuda in the vLLM environment: 12.8" in cuda
    assert "D9" in cuda
    assert "'hardware record' PASS" in sources["runtime.driver_version"]
    assert "'worst-case p95' PASS" in sources["runtime.max_model_len"]
    assert "Hugging Face" in sources["model.license"]
    sorted_lines = "".join(f"{line}\n" for line in sorted(PACKAGES))
    assert values[freeze_assembly.LOCK_ITEM] == hashlib.sha256(sorted_lines.encode()).hexdigest()
    assert f"vllm=={VLLM}" in sources[freeze_assembly.LOCK_ITEM]
    inputs = _inputs_doc()["items"]
    for key in freeze_assembly.INPUT_ITEMS:
        assert values[key] == inputs[key]["value"] and sources[key] == inputs[key]["source"]
    assert values["meanings.sha256"] is None  # a config item: copied from the config by build
    pending = {k for k, v in values.items() if v is None}
    assert {freeze_assembly.role_of(k) for k in pending} == {"owner at G4"}
    assert pending == {
        s.key for s in freeze.ITEM_SPECS if s.guard == "config" or s.draft_pending
    } | {"fallback.banks_sha256"}
    # what `freeze check` and the CI guard accept
    assert freeze.manifest_problems(filled) == []
    assert freeze.freeze_differences(kit.draft, freeze.current_values()) == []
    assert freeze.main(["check", str(kit.draft)]) == 0
    assert "recorded model.weights_sha256 = 2eb5af888340... (new)" in out
    assert f"recorded {freeze_assembly.LOCK_ITEM} = " in out and "(new)" in out
    assert "is a config item: give --config" in out
    assert "is not the committed generation/llm/manifest.json" in out
    assert "items pending" in out


def test_fill_reports_confirmed_and_changed_values(built_draft):
    draft = freeze.record_value(built_draft, "runtime.precision", "float16", "earlier value")
    draft = freeze.record_value(draft, "model.license", "apache-2.0", "earlier value")
    values = {
        "runtime.precision": freeze.FreezeValue("bfloat16", "GPU host: test"),
        "model.license": freeze.FreezeValue("apache-2.0", "GPU host: test"),
    }
    filled, lines = freeze_assembly.fill_draft(draft, values)
    assert lines == [
        "recorded model.license = apache-2.0 (confirmed)",
        "recorded runtime.precision = bfloat16 (changed from float16)",
    ]
    assert freeze.item_values(filled)["runtime.precision"] == "bfloat16"


def test_committed_draft_confirms_its_development_time_values(kit, capsys):
    """The model values the committed draft carries from Hugging Face metadata are the ones
    the GPU host's manifest gives (confirmed at G4, not changed)."""
    committed = ROOT / "generation" / freeze.DRAFT_NAME
    if not committed.is_file():
        pytest.skip("no committed draft (after G4)")
    shutil.copyfile(committed, kit.draft)
    assert freeze_assembly.main(["fill", "--draft", str(kit.draft), *kit.gpu_args()]) == 0
    out = capsys.readouterr().out
    for key in ("model.tokenizer_revision", "model.weights_sha256", "model.license"):
        assert re.search(rf"recorded {re.escape(key)} = \S+ \(confirmed\)", out), key


def test_fill_inputs_only_and_out(kit, capsys):
    before = kit.draft.read_bytes()
    out = kit.draft.parent / "filled.json"
    args = ["fill", "--draft", str(kit.draft), "--inputs", str(kit.inputs), "--out", str(out)]
    assert freeze_assembly.main(args) == 0
    assert kit.draft.read_bytes() == before
    values = freeze.item_values(read_json(out))
    assert values["pilot.timing_review"]["panel_sessions"] == 6
    assert values["runtime.vllm_version"] is None
    assert "wrote filled.json" in capsys.readouterr().out


def test_fill_checks_the_meaning_set_of_the_frozen_config(kit, tmp_path, capsys):
    meanings = load_meanings(DEMO_MEANINGS).sha256()

    def config(sha: str) -> Path:
        built = gc.build_generation_config(
            "DEMO-assembly-config",
            llm_manifest_sha256=None,
            decoding_schema_sha256="d" * 64,
            prompts=gc.PromptHashes("a" * 64, "b" * 64),
            meanings_sha256=sha,
            separation_threshold="0.10",
            fallback=gc.FallbackPins("c" * 64, {p: "e" * 64 for p in ("P1", "P2", "P3")}),
        )
        path = tmp_path / f"config-{sha[:4]}.json"
        built.write(path)
        return path

    args = ["fill", "--draft", str(kit.draft), "--inputs", str(kit.inputs)]
    assert freeze_assembly.main([*args, "--config", str(config(meanings))]) == 0
    assert "names the meaning set of the inputs" in capsys.readouterr().out
    err = _refused(kit, capsys, "E_MISMATCH", args=[*args, "--config", str(config("f" * 64))])
    assert f"inputs {meanings}" in err
    _refused(kit, capsys, "E_INPUT", args=[*args, "--config", str(tmp_path / "none.json")])


def test_non_backing_criteria_do_not_block_the_fill(kit, capsys):
    text = _acceptance(kit.demo, {"outbound": "FAIL", "cap": "PENDING"})
    (kit.evidence / "acceptance.md").write_text(text, encoding="utf-8")
    host = freeze_assembly.gpu_host_values(kit.manifest, kit.evidence, kit.lock)
    assert set(host.values) == set(freeze_assembly.GPU_ITEMS)
    assert f"acceptance (no freeze item): FAIL {CRITERIA['outbound']}" in host.notes
    assert f"acceptance (no freeze item): PENDING {CRITERIA['cap']}" in host.notes


# ---------------------------------------------------------------------------
# Refusals


def test_refuses_missing_evidence_files(kit, capsys):
    (kit.evidence / "acceptance.md").unlink()
    (kit.evidence / "refusal-weights.log").unlink()
    err = _refused(kit, capsys, "E_EVIDENCE")
    assert "acceptance.md: not found" in err and "refusal-weights.log: not found" in err
    shutil.rmtree(kit.evidence)
    _refused(kit, capsys, "E_EVIDENCE")


@pytest.mark.parametrize(
    "criterion, items",
    [
        ("p95", "runtime.precision, runtime.max_model_len"),
        ("refusal", "model.tokenizer_revision, model.weights_sha256"),
        ("hardware", "runtime.vllm_version, runtime.cuda_version"),
        ("logged", "decoding.implementation"),
    ],
)
def test_refuses_evidence_that_does_not_pass_for_its_items(kit, capsys, criterion, items):
    text = _acceptance(kit.demo, {criterion: "FAIL"})
    (kit.evidence / "acceptance.md").write_text(text, encoding="utf-8")
    err = _refused(kit, capsys, "E_EVIDENCE")
    assert "is FAIL, so" in err and items in err


def test_refuses_a_missing_criterion_and_a_start_check_that_did_not_refuse(kit, capsys):
    text = _acceptance(kit.demo).replace(CRITERIA["p95"], "p95 latency")
    (kit.evidence / "acceptance.md").write_text(text, encoding="utf-8")
    (kit.evidence / "refusal-revision.exit").write_text("0\n", encoding="utf-8")
    err = _refused(kit, capsys, "E_EVIDENCE")
    assert "0 rows for the criterion 'worst-case p95'" in err
    assert "refusal-revision.exit: exit 0; the start check must refuse" in err
    (kit.evidence / "acceptance.md").write_text("no table\n", encoding="utf-8")
    assert "has no PASS, FAIL or PENDING rows" in _refused(kit, capsys, "E_EVIDENCE")


@pytest.mark.parametrize(
    "summary, acceptance",
    [
        ({"environment": "MOCK (mock_llm on loopback; no GPU, no model)"}, None),
        ({"runtime": "mock av-generation-mock-llm 1"}, None),
        ({}, "p95 9 ms at 16373 prompt tokens; MOCK run, not GPU evidence"),
    ],
)
def test_refuses_a_mock_run(kit, capsys, summary, acceptance):
    path = kit.evidence / freeze_assembly.SUMMARY
    _write_json(path, {**read_json(path), **summary})
    if acceptance is not None:
        text = _acceptance(kit.demo).replace("p95 12000 ms at 16373 prompt tokens", acceptance)
        (kit.evidence / "acceptance.md").write_text(text, encoding="utf-8")
    assert "a MOCK run, not GPU evidence" in _refused(kit, capsys, "E_MOCK")


def test_refuses_evidence_of_other_hardware(kit, capsys):
    other = _demo_manifest(driver_version="565.00", vram_mib=24_000)
    (kit.evidence / "acceptance.md").write_text(_acceptance(other), encoding="utf-8")
    path = kit.evidence / freeze_assembly.SUMMARY
    _write_json(path, {**read_json(path), "environment": _label(other), "runtime": "vllm 0.0.1"})
    err = _refused(kit, capsys, "E_MISMATCH")
    assert "runtime 'vllm 0.0.1'" in err
    assert "does not name 'driver 570.00'" in err and "does not name 'DEMO GPU 23034 MiB'" in err
    assert err.count("does not name") == 4  # label: GPU, driver; acceptance: GPU, driver


def test_refuses_a_lock_of_another_environment(kit, capsys):
    kit.lock.write_text("vllm==0.0.1\ntorch==2.8.0\n", encoding="utf-8")
    err = _refused(kit, capsys, "E_MISMATCH")
    assert f"vllm==0.0.1, LLM manifest runtime.version {VLLM}" in err
    assert f"torch==2.8.0, LLM manifest hardware.torch_version {TORCH}+cu128" in err
    kit.lock.write_text("torch==2.9.0\n", encoding="utf-8")
    assert "no vllm==<version> line" in _refused(kit, capsys, "E_MISMATCH")
    kit.lock.write_text("#av only notes\n\n", encoding="utf-8")
    assert "no package lines" in _refused(kit, capsys, "E_INPUT")
    kit.lock.unlink()
    assert "cannot read the vLLM lock" in _refused(kit, capsys, "E_INPUT")


def test_refuses_values_the_committed_llm_manifest_contradicts(kit, capsys):
    changed = dataclasses.replace(
        kit.demo, runtime=dataclasses.replace(kit.demo.runtime, max_model_len=20_000)
    )
    changed.write(kit.manifest)
    err = _refused(kit, capsys, "E_MISMATCH")
    assert "runtime.max_model_len: 20000 here, 16896 in" in err


def test_refuses_a_manifest_without_recorded_hardware(kit, capsys):
    shutil.copyfile(ROOT / freeze.LLM_MANIFEST_PATH, kit.manifest)
    assert "run bringup.sh" in _refused(kit, capsys, "E_INPUT")
    kit.manifest.write_text("{}", encoding="utf-8")
    assert "cannot load the LLM manifest" in _refused(kit, capsys, "E_INPUT")


def test_refuses_to_write_inside_a_git_work_tree(kit, tmp_path, capsys):
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    inside = repo / "freeze-draft.json"
    shutil.copyfile(kit.draft, inside)
    args = ["fill", "--draft", str(inside), "--inputs", str(kit.inputs)]
    assert freeze_assembly.main(args) == 2
    assert "error: E_POLICY" in capsys.readouterr().err
    assert inside.read_bytes() == kit.draft.read_bytes()
    out = ["fill", "--draft", str(kit.draft), "--inputs", str(kit.inputs), "--out", str(inside)]
    _refused(kit, capsys, "E_POLICY", args=out)
    in_repo = ROOT / "generation" / "filled-draft-test.json"
    _refused(kit, capsys, "E_POLICY", args=[*out[:-1], str(in_repo)])
    assert not in_repo.exists()


def test_refuses_bad_inputs_files(kit, capsys):
    doc = _inputs_doc()
    doc["items"]["separation.evidence_sha256"]["value"] = "not-a-hash"
    _write_json(kit.inputs, doc)
    assert "is not a G4 inputs file" in _refused(kit, capsys, "E_INPUT")
    doc = _inputs_doc()
    doc["items"]["pilot.timing_review"]["value"]["max_atom_minutes"] = 21
    _write_json(kit.inputs, doc)
    assert "over the 20-minute atom budget" in _refused(kit, capsys, "E_INPUT")
    doc["items"]["pilot.timing_review"]["value"]["within_budget"] = False
    _write_json(kit.inputs, doc)
    assert "bookings_extended" in _refused(kit, capsys, "E_INPUT")  # schema: if/then
    doc["items"]["pilot.timing_review"]["value"]["bookings_extended"] = True
    doc["items"]["meanings.sha256"] = {"meaning_set": "missing-dir", "source": "DEMO"}
    _write_json(kit.inputs, doc)
    assert "cannot load the meaning set" in _refused(kit, capsys, "E_INPUT")
    kit.inputs.write_text("[]", encoding="utf-8")
    assert "is not a JSON object" in _refused(kit, capsys, "E_INPUT")
    kit.inputs.write_text("{", encoding="utf-8")
    assert "cannot read the inputs file" in _refused(kit, capsys, "E_INPUT")


def test_meanings_from_a_hash_or_a_meaning_set(kit):
    meanings = load_meanings(DEMO_MEANINGS).sha256()
    assert freeze_assembly.read_inputs(kit.inputs).meanings.value == meanings
    doc = _inputs_doc()
    doc["items"]["meanings.sha256"] = {"value": "9" * 64, "source": "DEMO"}
    _write_json(kit.inputs, doc)
    assert freeze_assembly.read_inputs(kit.inputs).meanings == freeze.FreezeValue("9" * 64, "DEMO")
    del doc["items"]["meanings.sha256"]
    _write_json(kit.inputs, doc)
    assert freeze_assembly.read_inputs(kit.inputs).meanings is None


def test_refuses_a_frozen_manifest_and_a_guard_failure(kit, capsys, monkeypatch):
    frozen = {**read_json(kit.draft), "status": "frozen"}
    _write_json(kit.draft, frozen)
    args = ["fill", "--draft", str(kit.draft), "--inputs", str(kit.inputs)]
    _refused(kit, capsys, "E_STATUS", args=args)
    _write_json(kit.draft, {**frozen, "status": "draft"})
    monkeypatch.setattr(freeze, "freeze_differences", lambda path, current: ["synthetic change"])
    assert "synthetic change" in _refused(kit, capsys, "E_GUARD", args=args)


def test_cli_argument_rules(kit, capsys):
    draft = ["fill", "--draft", str(kit.draft)]
    for args in (
        [*draft, "--llm-manifest", str(kit.manifest)],
        draft,
        [*draft, *kit.gpu_args(), "--config", "c.json"],
    ):
        with pytest.raises(SystemExit) as exit_info:
            freeze_assembly.main(args)
        assert exit_info.value.code == 2
    assert "go together" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Status


def _check_rows(manifest: dict) -> list[freeze_assembly.StatusRow]:
    rows = freeze_assembly.status_rows(manifest)
    assert [r.key for r in rows] == list(freeze.REQUIRED_ITEM_KEYS)
    for row in rows:
        assert row.state in ("repository", "recorded", "pending")
        assert (row.filled_by in ROLES) == (row.state == "pending"), row
        assert (row.state == "repository") == (row.guard in ("code", "file") and row.value != "-")
        assert not re.search("[0-9a-f]{40}", row.value), row
    return rows


def test_status_lists_every_item_with_its_state_and_role(built_draft, kit, capsys):
    rows = {r.key: r for r in _check_rows(built_draft)}
    assert rows["runtime.vllm_version"].filled_by == "GPU host"
    assert rows[freeze_assembly.LOCK_ITEM].filled_by == "GPU host"
    assert rows["separation.evidence_sha256"].filled_by == "threshold check"
    assert rows["pilot.timing_review"].filled_by == "pilot panels"
    assert rows["config.document"].filled_by == "owner at G4"
    assert rows["generation.code"].filled_by == "owner at G4"
    assert rows["renderer.hash"].state == "repository" and rows["renderer.hash"].value.endswith(
        "..."
    )
    assert rows["renderer.implementation"].value.startswith("object, ")
    assert rows["decoding.top_k"].value == "50"
    assert freeze_assembly.main(["status", "--draft", str(kit.draft)]) == 0
    table = capsys.readouterr().out
    for key in freeze.REQUIRED_ITEM_KEYS:
        assert f"| `{key}` |" in table
    pending = sum(r.state == "pending" for r in rows.values())
    assert f"{pending} pending (" in table and "freeze build --signoff" in table
    assert not re.search(r"\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b", table)  # no dates
    assert _fill(kit) == 0
    capsys.readouterr()
    filled = _check_rows(read_json(kit.draft))
    assert {r.filled_by for r in filled if r.state == "pending"} == {"owner at G4"}
    assert freeze_assembly.main(["status", "--draft", str(kit.draft)]) == 0
    assert "| `pilot.audit_sha256` | recorded | `DEMO-A-pilot " in capsys.readouterr().out


def test_status_of_the_active_manifest(capsys):
    assert freeze_assembly.main(["status"]) == 0
    table = capsys.readouterr().out
    assert freeze.active_manifest_path().name in table
    assert freeze_assembly.main(["status", "--draft", "missing.json"]) == 2


def test_short_values():
    short = freeze_assembly._short
    assert short(None) == "-" and short(True) == "true" and short(16_896) == "16896"
    assert short("x" * 70) == "x" * 57 + "..."
    assert short({"P1": "a" * 64}) == "P1 aaaaaaaaaaaa..."
    many = {f"set-{i}": "b" * 64 for i in range(9)}
    assert short(many).startswith("9 digests: ")
    assert short([1, 2]).startswith("array, 2 entries, sha256 ")


# ---------------------------------------------------------------------------
# vLLM lock


def test_lock_digest_ignores_order_notes_and_line_ends():
    text = "\n".join(PACKAGES)
    digest = freeze_assembly.lock_sha256(text)
    assert (
        digest == hashlib.sha256("".join(f"{p}\n" for p in sorted(PACKAGES)).encode()).hexdigest()
    )
    shuffled = "#av a note\r\n" + "\r\n".join(reversed(PACKAGES)) + "\r\n\r\n"
    assert freeze_assembly.lock_sha256(shuffled) == digest
    assert freeze_assembly.lock_sha256(text + "\nextra==1") != digest
    lock = freeze_assembly.parse_lock(freeze_assembly.lock_text(text, "12.8"))
    assert lock.sha256 == digest and lock.packages == len(PACKAGES)
    assert lock.versions["vllm"] == VLLM and lock.torch_cuda == "12.8"
    named = freeze_assembly.parse_lock("Foo_Bar[extra]==1.0\n-e /x\nbaz @ file:///x\n")
    assert dict(named.versions) == {"foo-bar": "1.0"} and named.torch_cuda is None


def test_write_lock(tmp_path, capsys, monkeypatch):
    calls = []

    def run(command):
        calls.append(command[1:])
        if command[1:3] == ["-m", "pip"]:
            return "\n".join(reversed(PACKAGES)) + "\n"
        return "12.8\n"

    out = tmp_path / "env" / "av-vllm-lock.txt"
    out.parent.mkdir()
    lock, previous = freeze_assembly.write_lock("<env>/bin/python", out, run=run)
    assert previous is None and calls[0] == ["-m", "pip", "freeze", "--all"]
    text = out.read_text(encoding="utf-8")
    assert text.startswith("#av ") and "#av torch.version.cuda: 12.8\n" in text
    assert text.endswith("".join(f"{p}\n" for p in sorted(PACKAGES)))
    assert lock.sha256 == freeze_assembly.read_lock(out).sha256

    def no_torch(command):
        if command[1] == "-c":
            raise OSError("no torch")
        return "vllm==0.0.1\n"

    lock2, previous = freeze_assembly.write_lock("<env>/bin/python", out, run=no_torch)
    assert previous == lock.sha256 != lock2.sha256 and lock2.torch_cuda is None

    def failing(command):
        raise OSError("no pip")

    with pytest.raises(freeze.FreezeError, match="E_INPUT"):
        freeze_assembly.write_lock("<env>/bin/python", out, run=failing)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(freeze.FreezeError, match="E_POLICY"):
        freeze_assembly.write_lock("<env>/bin/python", tmp_path / "repo" / "lock.txt", run=run)
    assert (
        freeze_assembly.write_lock("p", out, run=lambda c: "x\n" if c[1] == "-c" else "a==1")[
            0
        ].torch_cuda
        == "x"
    )

    monkeypatch.setattr(lm, "run_command", run)
    assert freeze_assembly.main(["lock", "--python", "<env>/bin/python", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert f"{freeze_assembly.LOCK_ITEM} {lock.sha256}" in printed
    assert "the environment changed: previous lock" in printed
    args = ["lock", "--python", "p", "--out", str(tmp_path / "repo" / "lock.txt")]
    assert freeze_assembly.main(args) == 2


def test_acceptance_rows_are_read_from_the_table():
    rows = freeze_assembly.acceptance_criteria(
        "# title\n\n| Status | Criterion | Evidence |\n| --- | --- | --- |\n"
        "|  PASS | a criterion |  note, with a comma  |\n| maybe | x | y |\n"
    )
    assert rows == (freeze_assembly.Criterion("PASS", "a criterion", "note, with a comma"),)


def test_backing_covers_the_gpu_host_items_once():
    backed = [item for b in freeze_assembly.BACKING for item in b.items]
    assert len(backed) == len(set(backed))
    assert set(backed) | {"model.license"} == set(freeze_assembly.MANIFEST_ITEMS)
    assert set(freeze_assembly.GPU_ITEMS) <= set(freeze.REQUIRED_ITEM_KEYS)
    for key in (*freeze_assembly.GPU_ITEMS, *freeze_assembly.INPUT_ITEMS):
        assert freeze.SPECS[key].guard == "recorded", key
    assert freeze.SPECS[freeze_assembly.MEANINGS_ITEM].guard == "config"
    assert "freeze_assembly.py" in freeze.GENERATION_CODE_EXCLUDED
    assert freeze.SPECS[freeze_assembly.LOCK_ITEM].pending.startswith("PENDING (G4 assembly)")
