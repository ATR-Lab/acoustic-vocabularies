"""What the bank builder reads and what the proposer sees: permutations, configs,
the proposer's model steps, the sentinel test and the no-message rule."""

import ast
import dataclasses
import importlib
import json
import shutil
from pathlib import Path

import av_sound
import av_sound.composer
import pytest
from av_generation.genconfig import FREEZE_CONFIG_KEY, ConfigMismatch
from av_generation.ids import IdError
from av_generation.jsonio import read_json, to_json_value
from av_generation.llm import RawOutcome, TokenCountError
from av_generation.llm_fake import ScriptedLlmClient
from av_generation.outcomes import LlmStatus, SlotOutcome
from av_generation.proposers import BCellState, RetainedOption
from av_generation.records import SlotRecord
from av_sound.recipe import Profile
from av_sound.tables import SAMPLES_PER_MS

from av_banks.builder import BankBuildError, bank_spec, default_seed_namespace
from av_banks.permutation import (
    PermutationError,
    check_bank_unit,
    expected_unit_id,
    load_permutation,
    parse_permutation,
)
from av_banks.proposer import (
    RAW_OUTPUT_LIMIT,
    LlmSlotProposer,
    ProposerConfigError,
)
from av_banks.verify import verify_bank

BANKS_SRC = Path(__file__).resolve().parents[2] / "banks" / "src" / "av_banks"


def _unit(kit, tmp_path, *, name="unit", **changes):
    doc = read_json(kit.DEMO_UNIT)
    doc.update(changes)
    if changes.get("demo") is False:
        doc["seed_label"] = "sha256:" + "5" * 64
    path = tmp_path / name / "permutation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# -- permutation and bank IDs ------------------------------------------------


@pytest.mark.parametrize(
    ("bank_id", "unit"),
    [
        ("bank-P001", "B-P01"),
        ("bank-P008", "B-P08"),
        ("bank-C001", "B-C01"),
        ("bank-C064", "B-C64"),
        ("bank-C065", "B-S01"),
        ("bank-C072", "B-S08"),
        ("DEMO-bank-01", None),
    ],
)
def test_bank_ids_follow_the_dyad_slot_sequence(bank_id, unit):
    assert expected_unit_id(bank_id) == unit


def test_bad_bank_ids():
    with pytest.raises(PermutationError):
        expected_unit_id("bank-C000")
    with pytest.raises(IdError):
        expected_unit_id("B-C01")


def test_demo_permutation_fixture(kit):
    unit = kit.permutation
    assert unit.unit_id == "B-C01" and unit.demo and unit.set == "confirmatory"
    assert len(unit.atom_order) == 16 and unit.atom_order[0] == "Q-r2"
    assert unit.labels["K-a1"] == "FLIP_CARD"
    assert unit.sha256 == "5563e86ecb39a9eb2ab4dc5ed39b42093bab6459b8c0612f132d7f7d64a322c7"
    assert load_permutation(kit.DEMO_UNIT.parent).sha256 == unit.sha256


def test_banks_bind_to_their_own_unit(kit, tmp_path):
    pilot = load_permutation(
        _unit(kit, tmp_path, name="p", demo=False, set="pilot", unit_id="B-P01")
    )
    conf = load_permutation(_unit(kit, tmp_path, name="c", demo=False, unit_id="B-C02"))
    check_bank_unit("bank-P001", pilot)
    check_bank_unit("bank-C002", conf)
    check_bank_unit("DEMO-bank-01", kit.permutation)
    cases = [
        ("bank-P001", conf, "pilot bank cannot use a confirmatory unit"),
        ("bank-C001", conf, "belongs to dyad slot B-C01"),
        ("bank-C001", kit.permutation, "cannot use a DEMO unit"),
        ("DEMO-bank-01", pilot, "DEMO banks are built from DEMO units only"),
    ]
    for bank_id, unit, message in cases:
        with pytest.raises(PermutationError, match=message):
            check_bank_unit(bank_id, unit)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: "not json", "permutation"),
        (lambda d: [], "JSON object"),
        (lambda d: dict(d, format="other"), "format"),
        (lambda d: dict(d, study="A"), "Study B"),
        (lambda d: dict(d, unit_id="A-C01"), "unit_id"),
        (lambda d: dict(d, set="spare"), "set"),
        (lambda d: dict(d, demo="yes"), "demo"),
        (lambda d: dict(d, atoms=d["atoms"][:15]), "16 atoms"),
        (lambda d: dict(d, atoms=[d["atoms"][0]] * 16), "repeated"),
        (lambda d: dict(d, atoms=[1] * 16), "object"),
        (lambda d: dict(d, atom_order=d["atom_order"][:15]), "atom_order"),
    ],
)
def test_bad_permutations(kit, change, message):
    doc = read_json(kit.DEMO_UNIT)
    data = change(doc)
    raw = data.encode() if isinstance(data, str) else json.dumps(data).encode()
    with pytest.raises(PermutationError, match=message):
        parse_permutation(raw)


def test_wrong_label_family_is_refused(kit):
    doc = read_json(kit.DEMO_UNIT)
    atoms = [dict(a) for a in doc["atoms"]]
    k_a1 = next(a for a in atoms if a["atom_id"] == "K-a1")
    k_a1["semantic_label"] = "SCAN"
    with pytest.raises(PermutationError, match="not a permutation"):
        parse_permutation(json.dumps(dict(doc, atoms=atoms)).encode())


def test_bank_spec_versions_and_namespaces(kit):
    spec = bank_spec("DEMO-bank-01", kit.permutation)
    assert (spec.bank_version, spec.seed_namespace) == ("1.0.0", "DEMO-bank-01")
    assert spec.set == "demo" and spec.labels["K-a1"] == "FLIP_CARD"
    rebuilt = bank_spec("DEMO-bank-01", kit.permutation, bank_version="1.1.0")
    assert rebuilt.seed_namespace == "DEMO-bank-01-v1.1.0"
    assert default_seed_namespace("bank-C001", "2.0.0") == "bank-C001-v2.0.0"
    custom = bank_spec("DEMO-bank-01", kit.permutation, seed_namespace="DEMO-ns.2")
    assert custom.seed_namespace == "DEMO-ns.2"
    with pytest.raises(BankBuildError, match="bank_version"):
        bank_spec("DEMO-bank-01", kit.permutation, bank_version="v2")
    with pytest.raises(BankBuildError, match="seed namespace"):
        bank_spec("DEMO-bank-01", kit.permutation, seed_namespace="bad|ns")
    with pytest.raises(IdError):
        bank_spec("-bad", kit.permutation)


# -- configuration checks ----------------------------------------------------


def test_demo_bank_needs_a_demo_config_and_run(kit, tmp_path):
    real = kit.make_config("pilot-1", prompt_set=kit.prompt_set, llm="b" * 64)
    script = kit.script(kit.all_kind("valid"))
    with pytest.raises(ConfigMismatch, match="E_CONFIG_KIND"):
        kit.builder(tmp_path, script, config=real).build()
    with pytest.raises(BankBuildError, match="cannot be built as pilot"):
        kit.builder(tmp_path, script, kind="pilot").build()
    with pytest.raises(BankBuildError, match="workers"):
        kit.builder(tmp_path, script, workers=4)
    assert not (tmp_path / "DEMO-bank-01").exists()


def test_confirmatory_bank_needs_the_frozen_config(kit, tmp_path):
    unit = load_permutation(_unit(kit, tmp_path, demo=False, unit_id="B-C01"))
    spec = bank_spec("bank-C001", unit)
    real = kit.make_config("frozen-1-0", prompt_set=kit.prompt_set, llm="b" * 64)
    script = kit.script(kit.all_kind("valid"))
    root = tmp_path / "out"
    with pytest.raises(ConfigMismatch, match="E_FREEZE_MISSING"):
        kit.builder(root, script, spec=spec, config=real).build()
    stale = {"status": "frozen", "items": [{"key": FREEZE_CONFIG_KEY, "value": "0" * 64}]}
    with pytest.raises(ConfigMismatch, match="E_FREEZE_MISMATCH"):
        kit.builder(root, script, spec=spec, config=real, freeze_manifest=stale).build()
    freeze = {
        "status": "frozen",
        "items": [{"key": FREEZE_CONFIG_KEY, "value": real.frozen_sha256()}],
    }
    result = kit.builder(
        root, script, spec=spec, config=real, freeze_manifest=freeze, run_id="C-banks-01"
    ).build()
    manifest = result.manifest
    assert (manifest.set, manifest.demo, manifest.dyad_slot) == ("confirmatory", False, "B-C01")
    assert manifest.generation_config_sha256 == real.frozen_sha256()
    assert verify_bank(result.bank_dir).ok


def test_proposer_inputs_must_match_the_config(kit):
    script = kit.script(kit.all_kind("valid"))
    other_prompts = kit.make_prompt_set(kit.meanings, instruction="another DEMO instruction")
    proposer = LlmSlotProposer(script.client(), other_prompts, kit.decoding_schema)
    with pytest.raises(ProposerConfigError, match="B prompt set"):
        proposer.check_config(kit.config)
    schema = dict(kit.decoding_schema, title="changed")
    with pytest.raises(ProposerConfigError, match="decoding schema"):
        LlmSlotProposer(script.client(), kit.prompt_set, schema).check_config(kit.config)
    LlmSlotProposer(script.client(), kit.prompt_set, kit.decoding_schema).check_config(kit.config)


# -- the proposer's model steps ----------------------------------------------


def _cell(slot=1):
    return BCellState("DEMO-bank-01", 1, Profile.P1, "K-a1", slot, "FLIP_CARD", (), ())


def _proposer(kit, script, *, token_counter=None):
    client = ScriptedLlmClient(script, token_counter=token_counter)
    proposer = LlmSlotProposer(
        client,
        kit.prompt_set,
        kit.decoding_schema,
        prompt_builder=kit.dump_prompt,
        parser=kit.strict_parser,
    )
    return proposer, client


def test_proposer_outcomes(kit):
    key = "B|DEMO-bank-01|1|P1|K-a1|1"
    recipe = json.dumps(kit.fresh_recipe("p", 1, "P1", "K-a1", 1))
    cases = [
        (recipe, None, LlmStatus.OK),
        ("{} {}", SlotOutcome.INVALID_JSON, LlmStatus.OK),
        (
            RawOutcome(LlmStatus.TIMEOUT, None, 40_000, None, None, 0),
            SlotOutcome.TIMEOUT,
            LlmStatus.TIMEOUT,
        ),
        (
            RawOutcome(LlmStatus.OVERFLOW_OUTPUT, "{", 9, 10, 512, 0, "length"),
            SlotOutcome.OVERFLOW_OUTPUT,
            LlmStatus.OVERFLOW_OUTPUT,
        ),
        (
            RawOutcome(LlmStatus.SERVER_ERROR, None, 9, None, None, 0),
            SlotOutcome.INVALID_JSON,
            LlmStatus.SERVER_ERROR,
        ),
    ]
    for entry, forced, status in cases:
        proposer, client = _proposer(kit, [entry])
        proposal = proposer.propose(_cell(), seed_key=key, slot_id="DEMO-bank-01.t1.P1.K-a1.s01")
        assert proposal.forced is forced and proposal.llm_status is status
        assert proposal.prompt_sha256 and proposal.schema_sha256 == proposer.schema_sha256
        assert client.calls[0].slot_id == "DEMO-bank-01.t1.P1.K-a1.s01"
        assert client.calls[0].seed_key == key
        if forced is None:
            assert proposal.candidate == json.loads(recipe)
    long = "x" * (RAW_OUTPUT_LIMIT + 10)
    proposer, _ = _proposer(kit, [long])
    assert len(proposer.propose(_cell(), seed_key=key, slot_id="s").raw_output) == RAW_OUTPUT_LIMIT


def test_proposer_counts_tokens_before_any_call(kit):
    key = "B|DEMO-bank-01|1|P1|K-a1|1"
    proposer, client = _proposer(kit, ["{}"], token_counter=lambda m: 16_385)
    proposal = proposer.propose(_cell(), seed_key=key, slot_id="s")
    assert proposal.forced is SlotOutcome.OVERFLOW_INPUT and proposal.tokens_in == 16_385
    assert client.call_count == 0

    def broken(messages):
        raise TokenCountError("server down")

    proposer, client = _proposer(kit, ["{}"], token_counter=broken)
    proposal = proposer.propose(_cell(), seed_key=key, slot_id="s")
    assert proposal.forced is SlotOutcome.INVALID_JSON
    assert proposal.llm_status is LlmStatus.SERVER_ERROR and client.call_count == 0
    proposer, client = _proposer(kit, ["{}"], token_counter=lambda m: 16_384)
    assert proposer.propose(_cell(), seed_key=key, slot_id="s").forced is None


# -- what the proposer sees (sentinel test) ----------------------------------

SENTINELS = ("SNTL-RATING-7F3A", "SNTL-PARTICIPANT-P0412", "SNTL-TESTANSWER-9C1E")
FORBIDDEN_KEYS = (
    "rating",
    "association",
    "distinguishab",
    "comfort",
    "score",
    "eligible",
    "rater",
    "participant",
    "learner",
    "answer",
    "response",
    "accuracy",
    "test",
)


def _keys(value, out):
    if isinstance(value, dict):
        for key, item in value.items():
            out.add(key)
            _keys(item, out)
    elif isinstance(value, list):
        for item in value:
            _keys(item, out)
    return out


def _field_names(cls, seen=None):
    seen = set() if seen is None else seen
    names = set()
    for f in dataclasses.fields(cls):
        names.add(f.name)
    for nested in (RetainedOption, SlotRecord):
        if nested not in seen:
            seen.add(nested)
            names |= _field_names(nested, seen)
    return names


def test_proposer_context_has_no_rating_participant_or_test_fields(kit, tmp_path, monkeypatch):
    unit_dir = tmp_path / "unit"
    unit_dir.mkdir()
    shutil.copy(kit.DEMO_UNIT, unit_dir / "permutation.json")
    # restricted files a careless builder might pick up next to the unit or the run
    (unit_dir / "confirmatory-dyads.json").write_text(
        json.dumps({"members": [{"participant_id": SENTINELS[1], "role": "active"}]}), "utf-8"
    )
    (unit_dir / "ratings.jsonl").write_text(
        json.dumps({"record": "rating", "association": 7, "comfort": SENTINELS[0]}) + "\n", "utf-8"
    )
    (unit_dir / "test-responses.json").write_text(json.dumps({"answer": SENTINELS[2]}), "utf-8")
    for i, sentinel in enumerate(SENTINELS):
        monkeypatch.setenv(f"AV_SENTINEL_{i}", sentinel)
    seen_cells = []

    def recording_prompt(cell, *, prompt_set):
        seen_cells.append(cell)
        return kit.dump_prompt(cell, prompt_set=prompt_set)

    order = kit.permutation.atom_order
    kinds = kit.mixed_kinds(11, 0.8)
    script = kit.script(kinds)
    spec = bank_spec("DEMO-bank-01", load_permutation(unit_dir))
    builder = kit.builder(tmp_path / "out", script, spec=spec)
    client = script.client()
    builder.proposer = LlmSlotProposer(
        client,
        kit.prompt_set,
        kit.decoding_schema,
        prompt_builder=recording_prompt,
        parser=kit.strict_parser,
    )
    result = builder.build()
    assert result.status == "complete" and seen_cells and client.calls
    texts = [m["content"] for call in client.calls for m in call.messages]
    texts += [json.dumps(to_json_value(cell)) for cell in seen_cells]
    texts += [p.read_text(encoding="utf-8") for p in result.bank_dir.rglob("*.json*")]
    for text in texts:
        for sentinel in SENTINELS:
            assert sentinel not in text
    keys = set()
    for cell in seen_cells:
        _keys(to_json_value(cell), keys)
    for call in client.calls:
        _keys(json.loads(call.messages[-1]["content"]), keys)
    keys |= _field_names(BCellState)
    for key in keys:
        assert not any(word in key.lower() for word in FORBIDDEN_KEYS), key
    # the proposer saw the validation history and the retained prefix
    assert any(cell.history for cell in seen_cells)
    assert any(cell.retained for cell in seen_cells)
    last = seen_cells[-1]
    assert {o.atom_id for o in last.retained} >= set(order[:15])


# -- never a complete message ------------------------------------------------


def test_builder_never_composes_or_renders_a_complete_message(kit, tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the bank builder composed a message")

    for name in (
        "compose_message",
        "compose",
        "composite_hash",
        "write_message_wav",
        "message_length",
    ):
        if hasattr(av_sound.composer, name):
            monkeypatch.setattr(av_sound.composer, name, refuse)
        if hasattr(av_sound, name):
            monkeypatch.setattr(av_sound, name, refuse)
    longest = 900 * SAMPLES_PER_MS
    renders = []

    def atom_only(render):
        def wrapper(recipe, profile):
            out = render(recipe, profile)
            assert out.n_samples <= longest, "a render longer than one atom"
            renders.append(out.n_samples)
            return out

        return wrapper

    validate_module = importlib.import_module("av_sound.validate")
    renderer_module = importlib.import_module("av_sound.renderer")
    monkeypatch.setattr(validate_module, "render", atom_only(renderer_module.render))
    result = kit.build(tmp_path, kit.script(kit.all_kind("valid")))
    assert verify_bank(result.bank_dir).ok
    assert len(renders) >= 2 * 192


def test_av_banks_never_imports_the_composer():
    banned = {
        "compose_message",
        "compose",
        "composite_hash",
        "write_message_wav",
        "message_length",
        "build_dyad_package",
        "build_package",
    }
    for path in sorted(BANKS_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "av_sound.composer", path.name
                assert not banned & {a.name for a in node.names}, path.name
            if isinstance(node, ast.Attribute):
                assert node.attr not in banned, path.name
