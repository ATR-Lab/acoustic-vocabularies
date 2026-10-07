"""Study-mode batch runner (#20): a batch built from the real components of #16-#19.

The acceptance run wires the real slot ledger (#17), A2 (#18), A3 (#17) through #16's
client against #16's mock OpenAI-compatible server (loopback only), and A1 through the
real A1 HTTP service (#19) worked by a bot designer, with bot raters on the panel session
contract, in accelerated real time (`ScaledClock`). It must reach the issue's counts
(48 commits, 576 slot records) with logs the audit (#24) can read. The other tests cover
the start checks (with the G4 freeze guard of #25), the LLM server probe, the panel
serving path and the command line. Keyed station sessions with bot stations on the real
panel server (#21) are in `test_rater_panel_runner.py`.
"""

import collections
import dataclasses
import json
import os
import shutil
import socket
import threading
from pathlib import Path

import httpx
import pytest
from av_sound.store import snapshot_digest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from av_generation import _batch_sim as sim
from av_generation import batch_runner as br
from av_generation import freeze as g4
from av_generation.clock import ManualClock, ScaledClock
from av_generation.config import RaterSeat
from av_generation.genconfig import (
    FREEZE_CONFIG_KEY,
    ConfigMismatch,
    PromptHashes,
    build_generation_config,
    fallback_pins,
)
from av_generation.ids import STUDY_A_METHODS, Method, RunKind, Study
from av_generation.jsonio import document_text, file_sha256, read_json, schema_sha256
from av_generation.llm import decoding_schema, decoding_schema_sha256
from av_generation.llm_manifest import load_llm_manifest, manifest_sha256
from av_generation.mock_llm import MOCK_RUNTIME, MockLlmServer
from av_generation.orchestrator import (
    OrchestratorError,
    build_batch_config,
    read_batch_table,
    read_book_key,
)
from av_generation.panel import seat_key, station_url
from av_generation.prompts import default_prompt_set_dir, load_prompt_set
from av_generation.rater_protocol import STATION_PAGE
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    FallbackScanRecord,
    LlmRequest,
    PlayEvent,
    RatingRecord,
    RunManifest,
    SlotRecord,
    SlotRefusal,
    TimingEvent,
    cap_key,
    read_records,
)
from av_generation.rundir import RunPolicyError

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests/generation/fixtures/orchestrator"
RUN_ID = "DEMO-A-real-01"
SPEED = 200
A1_INVALID = "DEMO-BK-7QX4.Q-a2.r1s2"
A1_TIMEOUT = "DEMO-BK-7QX4.Q-a2.r2s3"
RECORD_TYPES = {
    "slot": SlotRecord,
    "slot_refusal": SlotRefusal,
    "llm_request": LlmRequest,
    "rating": RatingRecord,
    "decision": DecisionRecord,
    "commit": CommitRecord,
    "fallback_scan": FallbackScanRecord,
    "play": PlayEvent,
    "timing": TimingEvent,
}


def demo_inputs(proposers="real", **overrides):
    return br.load_batch_inputs(
        config=ROOT / "generation/examples/demo-batch-config.json",
        meanings=ROOT / "generation/examples/demo-meanings",
        fallback=ROOT / "sound" / sim.DEMO_FALLBACK_MANIFEST,
        proposers=proposers,
        **overrides,
    )


@pytest.fixture(scope="module")
def mock_llm_url():
    """#16's mock OpenAI-compatible server on loopback for this module."""
    from av_generation.webserve import serve_in_thread

    server = MockLlmServer()
    with serve_in_thread(server.app) as url:
        yield url


def _logs(layout):
    return {
        name: read_records(layout.log(name), cls) if layout.log(name).exists() else []
        for name, cls in RECORD_TYPES.items()
    }


# ---------------------------------------------------------------------------
# The acceptance run: real ledger, A1 (HTTP), A2, A3 (mock server), bot raters


@pytest.fixture(scope="module")
def real_run(tmp_path_factory, mock_llm_url):
    out = tmp_path_factory.mktemp("runs")
    if os.environ.get("CI") == "true":
        out = ROOT / "generation/out/ci/batch-runner"
        shutil.rmtree(out / RUN_ID, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
    inputs = demo_inputs()
    batch = br.open_batch(
        inputs,
        out / RUN_ID,
        kind=RunKind.SYNTHETIC,
        clock=ScaledClock(SPEED),
        llm_url=mock_llm_url,
        a1_station="S9",
    )
    lines = []
    nxt = br.run_session(
        batch,
        appointment="all",
        panel="bots",
        designer="bot",
        a1_port=0,
        designer_invalid_slots=frozenset({A1_INVALID}),
        designer_timeout_slots=frozenset({A1_TIMEOUT}),
        log=lines.append,
    )
    assert nxt is None
    logs = _logs(batch.layout)
    summary = sim.summarize(batch.layout, batch.orchestrator.config)
    summary["llm_requests"] = len(logs["llm_request"])
    summary["a1_late_submits"] = len(batch.bot_designer.late)
    summary["slot_outcomes_by_method"] = {
        m.value: dict(
            sorted(
                collections.Counter(s.outcome.value for s in logs["slot"] if s.method is m).items()
            )
        )
        for m in STUDY_A_METHODS
    }
    if os.environ.get("CI") == "true":
        (out / f"{RUN_ID}-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    return batch, logs, lines, summary


def test_real_components_reach_the_batch_counts(real_run):
    """Acceptance: 48 commits and 576 slot records, through the real components. Bot
    ratings that a slow runner delivers after a slot's lock become `missing` records (and
    can send an atom to the bank fallback); the counts hold either way."""
    batch, logs, _, summary = real_run
    counts = summary["counts"]
    assert counts["slot_records"] == 576
    assert counts["commits_in_final_books"] == 48
    assert set(counts["rating_records_per_rater"].values()) == {576}
    assert len(counts["rating_records_per_rater"]) == 3
    assert counts["decision_records"] == 192 and counts["message_plays"] == 0
    slots = logs["slot"]
    assert collections.Counter(s.method for s in slots) == dict.fromkeys(STUDY_A_METHODS, 192)
    # every atom of every book used exactly its 12 slots in the one shared ledger
    for book in batch.orchestrator.config.books:
        for atom in batch.orchestrator.config.atom_order:
            assert batch.ledger.used(cap_key(Study.A, atom, book_id=book.book_id)) == 12
    assert batch.ledger.open_tickets() == ()


def test_run_documents_record_the_real_inputs(real_run):
    batch, _, _, _ = real_run
    manifest = RunManifest.read(batch.layout.manifest)
    gen = batch.inputs.generation_config
    assert manifest.closed_utc is not None and manifest.clock == "scaled"
    assert manifest.clock_speed == SPEED and manifest.purpose == "batch"
    assert manifest.generation_config_sha256 == gen.frozen_sha256()
    assert manifest.llm_manifest_sha256 == manifest_sha256() == gen.llm_manifest_sha256
    assert gen.decoding_schema_sha256 == decoding_schema_sha256()
    prompts = load_prompt_set(default_prompt_set_dir(), meanings=batch.inputs.meanings)
    assert gen.prompts == prompts.hashes()
    # the closed manifest hashes every file of the run (what the audit and #13 trust)
    root = batch.layout.root
    assert manifest.files and all(file_sha256(root / rel) == h for rel, h in manifest.files.items())
    logs = [f"logs/{p.name}" for p in batch.layout.logs_dir.iterdir()]
    assert set(logs) <= set(manifest.files) and "logs/slots.jsonl" in logs


def test_a3_slots_join_their_model_calls(real_run):
    """A3 went through #17's proposer and #16's client: one logged call per slot that
    reached the model, joined on slot ID, seed and the shared hash definitions.

    In accelerated real time the token count's 5-s run-clock cap is 25 ms of real time
    at 200x, and each count opens a new HTTP client; a busy runner (always, on the
    Windows runner) misses it, and the slot is consumed as `invalid_json` with
    `llm_status` `server_error` and no call, as the slot rules say. The virtual-time run
    of the command-line test checks that every A3 slot reaches the model."""
    _, logs, _, _ = real_run
    requests = {r.slot_id: r for r in logs["llm_request"]}
    a3 = [s for s in logs["slot"] if s.method is Method.A3]
    schema_hash = schema_sha256(decoding_schema())
    called = 0
    for slot in a3:
        assert slot.seed_key.startswith("A3|DEMO-A-P01|") and slot.prompt_sha256
        assert slot.schema_sha256 == schema_hash
        request = requests.get(slot.slot_id)
        if slot.llm_status is None or slot.outcome.value == "overflow_input":
            assert request is None
            continue
        if request is None:  # a failed token count makes no call
            assert slot.outcome.value == "invalid_json" and slot.llm_status.value == "server_error"
            continue
        called += 1
        assert request.runtime == MOCK_RUNTIME and request.seed == slot.seed
        assert request.prompt_sha256 == slot.prompt_sha256
        assert request.schema_sha256 == slot.schema_sha256
        assert (request.temperature, request.top_p, request.top_k) == (0.7, 0.9, 50)
    assert called == len(requests) == len(logs["llm_request"])


def test_a2_and_a1_slots_come_from_the_real_proposers(real_run):
    batch, logs, _, _ = real_run
    a2 = [s for s in logs["slot"] if s.method is Method.A2]
    assert all(s.seed_key.startswith("A2|DEMO-A-P01|") and s.a2 is not None for s in a2)
    assert {s.a2.mode for s in a2} >= {"uniform"}
    a1 = {s.slot_id: s for s in logs["slot"] if s.method is Method.A1}
    assert all(s.designer_id == "D1" and s.seed_key is None for s in a1.values())
    bot = batch.bot_designer
    # the scripted invalid and skipped slots, through the real HTTP service and its timer
    assert a1[A1_TIMEOUT].outcome.value == "timeout"
    assert a1[A1_INVALID].outcome.value == "invalid_json" or A1_INVALID in bot.late
    late = set(bot.late)
    refusals = logs["slot_refusal"]
    assert {r.requested for r in refusals} == late and all(
        r.reason == "slot_closed" for r in refusals
    )
    # what the server answered the bot is what the ledger holds; a slot the bot did not
    # submit in time (skipped, late, or closed before a slow runner polled) timed out
    assert A1_TIMEOUT not in bot.submitted
    for slot_id, slot in a1.items():
        answered = bot.submitted.get(slot_id)
        if answered is None or answered == "late":
            assert slot.outcome.value == "timeout" and slot.latency_ms is None
        else:
            assert slot.outcome.value == answered
    assert any(s.outcome.value == "valid" for s in a1.values())
    # every A1 play joins one valid A1 slot with the same waveform (#19's join rule)
    previews = [p for p in logs["play"] if p.context == "a1_preview"]
    played = collections.Counter(p.slot_id for p in previews if p.result == "played")
    assert played and max(played.values()) == 1 and len(played) == bot.plays
    for play in previews:
        slot = a1[play.slot_id]
        assert slot.outcome.value == "valid"
        assert (slot.pcm_sha256, slot.file_sha256) == (play.pcm_sha256, play.asset_id)
        assert play.station == "S9" and play.actor_id == "D1"


def test_logs_are_audit_ready(real_run):
    """Every log validates against its schema, the panel and decision logs join the slot
    log, the store matches the commit log, and nothing the operator saw names a book."""
    batch, logs, lines, _ = real_run
    slots = {s.slot_id: s for s in logs["slot"]}
    assert len(slots) == 576 and all(s.run_id == RUN_ID for s in slots.values())
    for decision in logs["decision"]:
        assert all(c.slot_id in slots for c in decision.candidates)
        if decision.incumbent_slot_id is not None:
            assert slots[decision.incumbent_slot_id].outcome.value == "valid"
    scans = {(s.book_id, s.atom_id) for s in logs["fallback_scan"]}
    for commit in logs["commit"]:
        if commit.source == "selector":
            assert slots[commit.slot_id].recipe_sha256 == commit.recipe_sha256
        else:  # a bank fallback after missing ratings on a slow runner
            assert commit.source == "fallback_bank" and (commit.book_id, commit.atom_id) in scans
    for book in batch.orchestrator.config.books:
        snap = batch.store.snapshot_hashes(book.book_id)
        mine = {c.atom_id: c.pcm_sha256 for c in logs["commit"] if c.book_id == book.book_id}
        assert snapshot_digest(snap) == snapshot_digest(mine)
    events = collections.Counter(e.event for e in logs["timing"])
    assert events["run_start"] == events["run_end"] == 1
    assert events["proposal_window_start"] == events["proposal_window_end"] == 64
    assert events["feedback_sent"] == 3 * 16 * 3
    assert events["appointment_start"] == events["appointment_end"] == 4
    components = {e.component for e in logs["timing"]}
    assert {"orchestrator", "a1"} <= components
    # the runner's terminal lines name no book and no designer
    text = "\n".join(lines)
    assert "A1 designer page: http://127.0.0.1:" in text and "Atoms finished: 16/16" in text
    assert not [b.book_id for b in batch.orchestrator.config.books if b.book_id in text]
    assert "D1" not in text


# ---------------------------------------------------------------------------
# Start checks (with the G4 freeze guard, #25)


def _confirmatory_inputs(**changes):
    definition = read_batch_table(FIX / "demo-confirmatory-batch-table.csv")[0]
    books = read_book_key(FIX / "demo-confirmatory-book-key-A-C01.json", "A-C01")
    fallback = sim.demo_fallback()
    config = build_batch_config(
        definition,
        books,
        set_ns="A-C-test",
        panel_id="A-C01-N1",
        order_index=1,
        raters=tuple(RaterSeat(f"H{i}", f"S{i}", "human") for i in (1, 2, 3)),
        threshold="0.10",
        fallback=fallback,
        fallback_manifest_sha256="0" * 64,
    )
    meanings = sim.demo_meanings()
    prompts = load_prompt_set(default_prompt_set_dir(), meanings=meanings)
    gen = build_generation_config(
        "gen-runner-test",
        llm_manifest_sha256=manifest_sha256(),
        decoding_schema_sha256=decoding_schema_sha256(),
        prompts=prompts.hashes(),
        meanings_sha256=meanings.sha256(),
        separation_threshold="0.10",
        fallback=fallback_pins(fallback),
    )
    inputs = br.BatchInputs(
        config=config,
        generation_config=gen,
        meanings=meanings,
        fallback=fallback,
        prompt_set=prompts,
        llm_manifest=load_llm_manifest(),
        llm_manifest_sha256=manifest_sha256(),
    )
    return dataclasses.replace(inputs, **changes)


def _freeze(value, status="frozen"):
    return {"status": status, "items": [{"key": FREEZE_CONFIG_KEY, "value": value}]}


def test_confirmatory_start_needs_the_frozen_config(tmp_path):
    inputs = _confirmatory_inputs()
    frozen = inputs.generation_config.frozen_sha256()
    start = dict(kind="confirmatory", run_id="A-C01-run1")
    with pytest.raises(ConfigMismatch) as err:
        br.check_batch_start(inputs, **start)
    assert err.value.code == "E_FREEZE_MISSING"
    for freeze, code in (
        (_freeze(frozen, status="draft"), "E_FREEZE_STATUS"),
        (_freeze("0" * 64), "E_FREEZE_MISMATCH"),
    ):
        with pytest.raises(ConfigMismatch) as err:
            br.check_batch_start(dataclasses.replace(inputs, freeze_manifest=freeze), **start)
        assert err.value.code == code
    # the right config hash is not enough: the G4 freeze guard (#25) reads the manifest file
    with pytest.raises(br.RunnerError) as err:
        br.check_batch_start(dataclasses.replace(inputs, freeze_manifest=_freeze(frozen)), **start)
    assert err.value.code == br.E_FREEZE_GUARD and "--freeze-manifest" in str(err.value)
    # a refused start creates nothing
    with pytest.raises(ConfigMismatch):
        br.open_batch(inputs, tmp_path / "A-C01-run1", kind="confirmatory", clock=ManualClock())
    assert not (tmp_path / "A-C01-run1").exists()


# Synthetic values of the G4 items recorded on the GPU host or by people (test only).
_RECORDED_BY_KIND = {
    "sha256": "1" * 64,
    "sha256_map": {"test": "2" * 64},
    "revision": "3" * 40,
    "text": "test value",
    "decimal": "0.10",
    "integer": 16_896,
    "number": 1.0,
    "object": {"test": True},
}
_SIGNOFF = tuple(
    {
        "role": role,
        "date": "2027-01-25",
        "reference": f"https://github.com/ATR-Lab/acoustic-vocabularies/issues/25#test-{role}",
    }
    for role in ("owner", "advisor")
)


def _freeze_file(path, inputs, *, status="frozen", description=None):
    """A G4 freeze manifest (#25) of the inputs' generation config, built from this
    checkout by `freeze`'s own builder (synthetic recorded values, except those the
    committed LLM manifest records; test sign-off links, roles only), written to `path`."""
    recorded = {
        s.key: g4.FreezeValue(_RECORDED_BY_KIND[s.kind], "test value")
        for s in g4.ITEM_SPECS
        if s.guard == "recorded"
    }
    recorded.update(
        (key, value)
        for key, value in g4.committed_values().items()
        if g4.SPECS[key].guard == "recorded"
    )
    values = g4.freeze_values(recorded, inputs.generation_config, inputs.fallback)
    frozen = status == "frozen"
    manifest = g4.build_freeze_manifest(
        values,
        status=status,
        repo_commit="0123456789abcdef0123456789abcdef01234567" if frozen else None,
        tag="generation-freeze-v1.0" if frozen else None,
        signoff=_SIGNOFF if frozen else (),
        description=description,
    )
    path.write_text(document_text(manifest), encoding="utf-8", newline="\n")
    return path


def _with_freeze(inputs, path, manifest=None):
    """The inputs with the freeze manifest file `path`, as `load_batch_inputs` reads it
    (`manifest` replaces the document read from the file)."""
    return dataclasses.replace(
        inputs,
        freeze_manifest=read_json(path) if manifest is None else manifest,
        freeze_manifest_path=path,
    )


def test_confirmatory_start_runs_the_g4_freeze_guard(tmp_path, monkeypatch):
    """A confirmatory batch starts only on a frozen G4 manifest file that passes #25's
    freeze guard against this checkout and pins the batch's generation config."""
    inputs = _confirmatory_inputs()
    start = dict(kind="confirmatory", run_id="A-C01-run1")
    frozen = _freeze_file(tmp_path / "FREEZE-v1.0.json", inputs)
    draft = _freeze_file(tmp_path / "FREEZE-v1.0.draft.json", inputs, status="draft")
    br.check_batch_start(_with_freeze(inputs, frozen), **start)  # accepted
    # a draft is refused (step 4), and so is a draft file behind a frozen document (guard)
    with pytest.raises(ConfigMismatch) as err:
        br.check_batch_start(_with_freeze(inputs, draft), **start)
    assert err.value.code == "E_FREEZE_STATUS"
    with pytest.raises(br.RunnerError) as err:
        br.check_batch_start(_with_freeze(inputs, draft, read_json(frozen)), **start)
    assert err.value.code == br.E_FREEZE_GUARD and f"{g4.E_STATUS}: " in str(err.value)
    # the document the run records must be the file the guard checked
    other = _freeze_file(tmp_path / "other.json", inputs, description="FROZEN (test copy)")
    with pytest.raises(br.RunnerError) as err:
        br.check_batch_start(_with_freeze(inputs, other, read_json(frozen)), **start)
    assert err.value.code == br.E_FREEZE_GUARD and "is not the freeze manifest" in str(err.value)
    # a checkout that changed after the freeze never starts: here an edited selector,
    # which the generation config cannot see (`generation.code`)
    digests = g4.generation_code_digests()
    edited = dict(digests, **{"generation/src/av_generation/selector.py": "e" * 64})
    monkeypatch.setattr(g4, "generation_code_digests", lambda **_: edited)
    with pytest.raises(br.RunnerError) as err:
        br.check_batch_start(_with_freeze(inputs, frozen), **start)
    assert err.value.code == br.E_FREEZE_GUARD
    message = str(err.value)
    assert f"{g4.E_GUARD}: the running code or the repository differs" in message
    assert "changed: generation/src/av_generation/selector.py" in message
    with pytest.raises(br.RunnerError):  # refused before anything is created
        br.open_batch(
            _with_freeze(inputs, frozen),
            tmp_path / "runs" / "A-C01-run1",
            kind="confirmatory",
            clock=ManualClock(),
        )
    assert not (tmp_path / "runs").exists()
    monkeypatch.setattr(g4, "generation_code_digests", lambda **_: digests)
    br.check_batch_start(_with_freeze(inputs, frozen), **start)


def test_pilot_start_needs_no_frozen_manifest(tmp_path):
    """Pilot batches run before G4: no freeze manifest, no freeze guard (freeze.md
    section 7); a manifest given to them is compared by config hash only."""
    confirmatory = _confirmatory_inputs()
    inputs = dataclasses.replace(
        confirmatory, config=dataclasses.replace(confirmatory.config, set="pilot")
    )
    start = dict(kind="pilot", run_id="A-P01-run1")
    br.check_batch_start(inputs, **start)
    draft = _freeze_file(tmp_path / "FREEZE-v1.0.draft.json", inputs, status="draft")
    br.check_batch_start(_with_freeze(inputs, draft), **start)
    with pytest.raises(ConfigMismatch) as err:  # a manifest of another config is refused
        br.check_batch_start(
            dataclasses.replace(inputs, freeze_manifest=_freeze("0" * 64)), **start
        )
    assert err.value.code == "E_FREEZE_MISMATCH"


def test_cli_check_runs_the_g4_freeze_guard(tmp_path, monkeypatch, capsys):
    inputs = _confirmatory_inputs()
    inputs.config.write(tmp_path / "config.json")
    inputs.generation_config.write(tmp_path / "generation-config.json")
    frozen = _freeze_file(tmp_path / "FREEZE-v1.0.json", inputs)
    draft = _freeze_file(tmp_path / "FREEZE-v1.0.draft.json", inputs, status="draft")
    files = [
        "--kind", "confirmatory",
        "--config", str(tmp_path / "config.json"),
        "--generation-config", str(tmp_path / "generation-config.json"),
        "--meanings", str(ROOT / "generation/examples/demo-meanings"),
        "--fallback", str(ROOT / "sound" / sim.DEMO_FALLBACK_MANIFEST),
    ]  # fmt: skip
    check = ["check", *_cli(tmp_path, "A-C01-run1"), *files]
    assert br.main(check) == 1
    assert "E_FREEZE_MISSING" in capsys.readouterr().err
    assert br.main([*check, "--freeze-manifest", str(draft)]) == 1
    assert "E_FREEZE_STATUS" in capsys.readouterr().err
    assert br.main([*check, "--freeze-manifest", str(frozen)]) == 0
    assert capsys.readouterr().out.startswith("ok: run A-C01-run1 (confirmatory)")
    edited = dict(g4.generation_code_digests())
    edited["generation/src/av_generation/a2.py"] = "e" * 64
    monkeypatch.setattr(g4, "generation_code_digests", lambda **_: edited)
    assert br.main([*check, "--freeze-manifest", str(frozen)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"error: {br.E_FREEZE_GUARD}: refused by the G4 freeze guard: E_GUARD")
    assert "changed: generation/src/av_generation/a2.py" in err
    assert not (tmp_path / "A-C01-run1").exists()


def test_start_checks_refuse_inputs_that_disagree(tmp_path):
    inputs = _confirmatory_inputs(freeze_manifest=None)
    gen = inputs.generation_config
    with pytest.raises(RunPolicyError):  # DEMO- run IDs are for demo/synthetic runs only
        br.check_batch_start(inputs, kind="pilot", run_id="DEMO-x-01")
    with pytest.raises(OrchestratorError) as err:  # a confirmatory batch is not a pilot run
        br.check_batch_start(inputs, kind="pilot", run_id="A-C01-run1")
    assert err.value.code == "E_KIND"
    for changes, text in (
        ({"prompt_set": None}, "prompt set"),
        ({"llm_manifest_sha256": "1" * 64}, "LLM-manifest"),
        (
            {"generation_config": dataclasses.replace(gen, decoding_schema_sha256="2" * 64)},
            "decoding",
        ),
        (
            {
                "generation_config": dataclasses.replace(
                    gen, prompts=PromptHashes("3" * 64, "4" * 64)
                )
            },
            "prompt-set hashes",
        ),
    ):
        with pytest.raises(br.RunnerError) as err:
            br.check_batch_start(
                dataclasses.replace(inputs, **changes), kind="confirmatory", run_id="A-C01-run1"
            )
        assert err.value.code == "E_INPUTS" and text in str(err.value)
    with pytest.raises(br.RunnerError) as err:  # stand-ins never run a study batch
        br.check_batch_start(inputs, kind="confirmatory", run_id="A-C01-run1", proposers="sim")
    assert err.value.code == "E_MODE"
    inputs.config.write(tmp_path / "config.json")
    with pytest.raises(br.RunnerError) as err:  # a study batch brings its generation config
        br.load_batch_inputs(
            config=tmp_path / "config.json",
            meanings=ROOT / "generation/examples/demo-meanings",
            fallback=ROOT / "sound" / sim.DEMO_FALLBACK_MANIFEST,
        )
    assert err.value.code == "E_INPUTS"


def test_demo_inputs_build_their_generation_config(tmp_path):
    real = demo_inputs()
    assert real.generation_config.name == br.DEMO_GENERATION_CONFIG_NAME
    assert real.generation_config.prompts == real.prompt_set.hashes()
    br.check_batch_start(real, kind="synthetic", run_id="DEMO-chk-01")
    stand_in = demo_inputs("sim")
    assert stand_in.prompt_set is None and stand_in.llm_manifest is None
    assert stand_in.generation_config == sim.demo_generation_config(
        stand_in.meanings, stand_in.fallback
    )
    br.check_batch_start(stand_in, kind="demo", run_id="DEMO-chk-01", proposers="sim")
    # a given generation config is read and checked against the meaning and prompt sets
    real.generation_config.write(tmp_path / "generation-config.json")
    given = demo_inputs(generation_config=tmp_path / "generation-config.json")
    assert given.generation_config == real.generation_config
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps(_freeze("0" * 64)), encoding="utf-8")
    assert demo_inputs(freeze_manifest=freeze).freeze_manifest == _freeze("0" * 64)
    freeze.write_text("[]", encoding="utf-8")
    with pytest.raises(br.RunnerError):
        demo_inputs(freeze_manifest=freeze)


# ---------------------------------------------------------------------------
# LLM server probe


def _stub_llm(version, model):
    app = FastAPI()

    @app.get("/version")
    def _version():
        return {"version": version}

    @app.get("/v1/models")
    def _models():
        return {"object": "list", "data": [{"id": model}]}

    return app


def test_llm_probe_accepts_the_pinned_runtime_and_the_mock_for_demo_runs(serve_app, mock_llm_url):
    manifest = load_llm_manifest()
    assert br.probe_llm_server(mock_llm_url, manifest, kind="synthetic") == MOCK_RUNTIME
    with pytest.raises(br.RunnerError) as err:
        br.probe_llm_server(mock_llm_url, manifest, kind="pilot")
    assert err.value.code == "E_LLM_SERVER" and "mock" in str(err.value)
    pinned = serve_app(_stub_llm(manifest.runtime.version, manifest.model.id))
    assert br.probe_llm_server(pinned, manifest, kind="pilot") == manifest.runtime_label()
    for app in (_stub_llm("0.0.1", manifest.model.id), _stub_llm(manifest.runtime.version, "x/y")):
        with pytest.raises(br.RunnerError):
            br.probe_llm_server(serve_app(app), manifest, kind="pilot")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(br.RunnerError) as err:
        br.probe_llm_server(f"http://127.0.0.1:{port}", manifest, kind="synthetic", timeout_s=1)
    assert err.value.code == "E_LLM_SERVER"


# ---------------------------------------------------------------------------
# Panel serving path (the panel server itself is #21)


def test_panel_is_served_and_the_session_waits_for_every_station(tmp_path, monkeypatch):
    calls = []

    def stub_panel_app(host, *, clock, access_secret):
        calls.append((host, clock, access_secret))
        app = FastAPI()
        app.get(STATION_PAGE)(lambda: PlainTextResponse("station"))
        return app

    monkeypatch.setattr(br, "create_panel_app", stub_panel_app)
    batch = sim.make_sim_batch(tmp_path, "DEMO-panel-01", clock=ManualClock())
    orch = batch.orchestrator
    host = orch.panel_host()
    with br.serve_panel(host, clock=batch.clock, port=0) as served:
        base = served.base_url
        assert httpx.get(f"{base}{STATION_PAGE}", trust_env=False).text == "station"
    called_host, called_clock, secret = calls[0]
    assert (called_host, called_clock) == (host, batch.clock) and len(secret) == 32
    assert list(served.station_urls) == ["S1", "S2", "S3"]
    assert served.station_urls["S1"] == station_url(
        base, "S1", "R01", key=seat_key(secret, "R01", "S1")
    )
    with br.serve_panel(host, clock=batch.clock, port=0):  # a fresh secret per session
        assert len(calls) == 2 and calls[1][2] != secret
    with pytest.raises(br.RunnerError) as err:
        br.wait_for_stations(orch, timeout_s=0.2, poll_s=0.05)
    assert err.value.code == "E_STATIONS" and "S1, S2, S3" in str(err.value)
    for seat in host.seats():
        host.station_joined(seat.rater_id, seat.station, seat.kind)
    br.wait_for_stations(orch, timeout_s=1)
    # after the end: the session waits (bounded) until the stations have left
    assert br.wait_for_stations_to_leave(orch, timeout_s=0.2, poll_s=0.05) == ("S1", "S2", "S3")
    seats = list(host.seats())
    host.station_left(seats[0].rater_id, seats[0].station)
    left = threading.Timer(
        0.1, lambda: [host.station_left(s.rater_id, s.station) for s in seats[1:]]
    )
    left.start()
    assert br.wait_for_stations_to_leave(orch, timeout_s=5, poll_s=0.02) == ()
    left.join()


def test_a_station_session_serves_the_panel_and_starts_when_every_seat_joined(
    tmp_path, monkeypatch
):
    """`panel="stations"`: the panel app is served, `on_panel` gets its URL (where the
    dry run starts its bot stations), and the appointment starts once all seats joined.
    Here an in-process panel stands in for the three stations (#21 serves the real ones)."""
    monkeypatch.setattr(br, "create_panel_app", lambda host, *, clock, access_secret: FastAPI())
    inputs = demo_inputs("sim")
    clock = ScaledClock(1000)
    batch = br.open_batch(
        inputs, tmp_path / "DEMO-stations-01", kind="demo", clock=clock, proposers="sim"
    )
    stations = []

    def start_stations(base):
        assert base.startswith("http://127.0.0.1:")
        panel = sim.SyntheticPanel(batch.orchestrator.panel_host(), clock, sim.seeded_policy("x"))
        stations.append(panel.start())

    lines = []
    try:
        nxt = br.run_session(
            batch,
            appointment=1,
            panel="stations",
            panel_port=0,
            on_panel=start_stations,
            station_timeout_s=10,
            station_end_grace_s=0.3,
            log=lines.append,
        )
    finally:
        for panel in stations:
            panel.stop()
    assert nxt == inputs.config.atom_order[4]
    # the stand-in stations never leave: the session waited its grace and said so
    assert lines[-2] == "Stations still connected after the end: S1, S2, S3"
    for line, (station, rater) in zip(
        lines[:3], (("S1", "R01"), ("S2", "R02"), ("S3", "R03")), strict=True
    ):
        assert line.startswith(f"Rater station {station} ({rater}): http://127.0.0.1:")
        assert f"{STATION_PAGE}?station={station}&rater={rater}&key=" in line
    assert lines[3] == "Waiting for stations: S1, S2, S3"
    assert lines[-1] == f"Atoms finished: 4/16; next: {nxt}"
    ratings = read_records(batch.layout.log("rating"), RatingRecord)
    assert set(collections.Counter(r.rater_id for r in ratings).values()) == {4 * 36}


def test_bots_never_run_a_study_batch(tmp_path):
    batch = sim.make_sim_batch(tmp_path, "DEMO-bots-01", clock=ManualClock())
    study = br.StudyBatch(
        orchestrator=batch.orchestrator,
        layout=batch.layout,
        inputs=None,
        kind=RunKind.PILOT,
        clock=batch.clock,
        store=batch.store,
        proposers=batch.proposers,
        resumed=False,
    )
    for options in ({"panel": "bots"}, {"panel": "stations", "designer": "bot"}):
        study.a1 = object() if "designer" in options else None
        with pytest.raises(br.RunnerError) as err:
            br.run_session(study, **options)
        assert err.value.code == "E_MODE"
    demo = dataclasses.replace(study, kind=RunKind.DEMO, a1=None)
    with pytest.raises(br.RunnerError):  # nobody would move a manual clock
        br.run_session(demo, panel="stations")


# ---------------------------------------------------------------------------
# Command line


def _cli(tmp_path, run_id, *extra):
    return ["--run-dir", str(tmp_path / run_id), *extra]


def test_cli_check_and_input_errors(tmp_path, capsys):
    assert br.main(["check", *_cli(tmp_path, "DEMO-chk-01"), "--kind", "synthetic"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ok: run DEMO-chk-01 (synthetic), batch DEMO-A-P01")
    assert demo_inputs().generation_config.frozen_sha256() in out
    assert not (tmp_path / "DEMO-chk-01").exists()
    with pytest.raises(SystemExit) as stop:  # real runs name every input
        br.main(["check", *_cli(tmp_path, "A-P01-run1"), "--kind", "pilot"])
    assert stop.value.code == 2
    assert br.main(["check", *_cli(tmp_path, "A-P01-run1"), "--kind", "pilot", *_demo_files()]) == 1
    assert "E_KIND: a demo batch cannot run as pilot" in capsys.readouterr().err


def _demo_files():
    return [
        "--config",
        str(ROOT / "generation/examples/demo-batch-config.json"),
        "--meanings",
        str(ROOT / "generation/examples/demo-meanings"),
        "--fallback",
        str(ROOT / "sound" / sim.DEMO_FALLBACK_MANIFEST),
    ]


def test_cli_runs_appointments_with_the_real_components_and_resumes(tmp_path, capsys, mock_llm_url):
    """Virtual time (`--clock manual`, driven by the bot panel): appointment 1 in one
    process, then `--resume` runs appointment 2 on the same ledger and logs. No cap can
    fire while virtual time stands still, so every A3 slot reaches the mock model: the
    real A3 path (prompt, token count, call, parser, validator, ledger) end to end."""
    common = [
        "--kind",
        "synthetic",
        "--llm-url",
        mock_llm_url,
        "--designer",
        "bot",
        "--panel",
        "bots",
        "--clock",
        "manual",
        "--a1-port",
        "0",
    ]
    run_dir = tmp_path / "DEMO-cli-01"
    assert br.main(["run", *_cli(tmp_path, "DEMO-cli-01"), *common, "--appointment", "1"]) == 0
    out = capsys.readouterr().out
    assert "A1 designer page: http://127.0.0.1:" in out and "Atoms finished: 4/16" in out
    assert len(read_records(run_dir / "logs/slots.jsonl", SlotRecord)) == 4 * 36
    # the run exists: a second start is refused, a resume continues it
    assert br.main(["run", *_cli(tmp_path, "DEMO-cli-01"), *common]) == 1
    assert "already exists" in capsys.readouterr().err
    assert br.main(["run", *_cli(tmp_path, "DEMO-cli-01"), *common, "--resume"]) == 0
    assert "Atoms finished: 8/16" in capsys.readouterr().out
    slots = read_records(run_dir / "logs/slots.jsonl", SlotRecord)
    assert len(slots) == 8 * 36 and len({s.slot_id for s in slots}) == len(slots)
    a3 = [s for s in slots if s.method is Method.A3]
    requests = read_records(run_dir / "logs/llm-requests.jsonl", LlmRequest)
    assert {s.llm_status.value for s in a3} == {"ok"} and len(requests) == len(a3) == 96
    assert {r.slot_id for r in requests} == {s.slot_id for s in a3}
    assert {r.runtime for r in requests} == {MOCK_RUNTIME}
    assert any(s.outcome.value == "valid" for s in a3)
    timing = read_records(run_dir / "logs/timing.jsonl", TimingEvent)
    assert [e.appointment for e in timing if e.event == "appointment_start"] == [1, 2]
    assert any(e.event == "resume" for e in timing)
    # a resume of a run that never started is refused
    assert br.main(["run", *_cli(tmp_path, "DEMO-cli-02"), *common, "--resume"]) == 1
    assert "E_RUN_DIR" in capsys.readouterr().err


def test_cli_options_of_the_stand_ins(tmp_path, capsys):
    """The stand-ins run through the same `open_batch` / `run_session` path as the
    station-session test; here only what the command line adds is checked."""
    sim_args = ["--kind", "demo", "--proposers", "sim"]
    assert br.main(["check", *_cli(tmp_path, "DEMO-sim-01"), *sim_args]) == 0
    assert f"generation config {demo_inputs('sim').generation_config.frozen_sha256()}" in (
        capsys.readouterr().out
    )
    for extra, code in (
        (["--clock", "manual"], "E_MODE"),  # nobody would move a manual clock
        (["--panel", "bots", "--clock", "scaled", "--kind", "pilot", *_demo_files()], "E_MODE"),
    ):
        assert br.main(["run", *_cli(tmp_path, "DEMO-sim-01"), *sim_args, *extra]) == 1
        assert code in capsys.readouterr().err
    real = ["--kind", "demo", "--clock", "manual", "--panel", "bots", "--designer", "bot"]
    assert br.main(["run", *_cli(tmp_path, "DEMO-sim-02"), *real]) == 1
    assert "E_LLM_SERVER" in capsys.readouterr().err  # real proposers need --llm-url
    assert not (tmp_path / "DEMO-sim-01").exists() and not (tmp_path / "DEMO-sim-02").exists()


def test_a_closed_run_is_left_untouched_when_reopened(real_run, mock_llm_url):
    """`--resume` on a finished batch serves nothing and writes nothing: the closed
    manifest's file hashes stay true."""
    batch, _, _, _ = real_run
    before = {p: p.read_bytes() for p in batch.layout.root.rglob("*") if p.is_file()}
    again = br.open_batch(
        batch.inputs,
        batch.layout.root,
        kind=RunKind.SYNTHETIC,
        clock=ScaledClock(SPEED),
        llm_url=mock_llm_url,
        resume=True,
    )
    lines = []
    assert br.run_session(again, panel="stations", log=lines.append) is None
    assert lines == ["Atoms finished: 16/16; the run is closed"]
    after = {p: p.read_bytes() for p in batch.layout.root.rglob("*") if p.is_file()}
    assert after == before
