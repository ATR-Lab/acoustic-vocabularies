"""G4 freeze manifest (#25): format, builder, CI freeze guard, fallback re-render check.

`test_ci_freeze_guard` is the CI freeze guard: it compares the active manifest
(`generation/FREEZE-vX.Y.json` once G4 has passed, the committed draft before) with the
repository on Linux, macOS and Windows. The other tests show that the guard passes on an
unchanged manifest and fails on a deliberately changed value, and that re-rendering the
fallback books with the running renderer reproduces their hashes. Every fallback set and
config here is the public DEMO example or a relabelled copy of it (never study material).
"""

import dataclasses
import json
import shutil
from pathlib import Path

import pytest
from av_sound.fallback import load_fallback, seed_fingerprint
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from av_generation import constants as C
from av_generation import freeze
from av_generation import genconfig as gc
from av_generation._schemas import load_schema, schema_errors
from av_generation.genconfig import FREEZE_CONFIG_KEY
from av_generation.ids import RunKind
from av_generation.jsonio import canonical_sha256, document_text, file_set_sha256, file_sha256
from av_generation.meanings import load_meanings

ROOT = Path(__file__).resolve().parents[2]
DRAFT = ROOT / "generation" / freeze.DRAFT_NAME
DEMO_FALLBACK = ROOT / "sound/testvectors/fallback/demo-manifest.json"
H = "e" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"
ISSUE = "https://github.com/ATR-Lab/acoustic-vocabularies/issues/25"
SIGNOFF = (
    {"role": "owner", "date": "2027-01-25", "reference": ISSUE + "#issuecomment-1"},
    {"role": "advisor", "date": "2027-01-25", "reference": ISSUE + "#issuecomment-2"},
)
TEST_ONLY_SEED = "test-only-not-a-study-seed-0123456789abcdef"  # never stored, never a study seed

SKELETON_KEYS = (
    "config.frozen_sha256",
    "renderer.version",
    "renderer.hash",
    "renderer.recipe_schema_hash",
    "renderer.golden_manifest_sha256",
    "validator.version",
    "validator.hash",
    "separation.threshold",
    "model.id",
    "model.revision",
    "model.tokenizer_revision",
    "model.weights_sha256",
    "model.license",
    "runtime.vllm_version",
    "runtime.cuda_version",
    "runtime.driver_version",
    "runtime.gpu",
    "runtime.precision",
    "runtime.max_model_len",
    "runtime.chat_template_sha256",
    "decoding.temperature",
    "decoding.top_p",
    "decoding.top_k",
    "decoding.repetition_penalty",
    "decoding.max_tokens",
    "decoding.max_input_tokens",
    "decoding.implementation",
    "schema.decoding_sha256",
    "prompts.a3_sha256",
    "prompts.b_sha256",
    "meanings.sha256",
    "llm.manifest_sha256",
    "seeds.function",
    "seeds.namespaces",
    "budget.study_a",
    "budget.study_b",
    "a2.rules",
    "selector.rules",
    "fallback.bank_hash",
    "fallback.books_sha256",
    "pilot.timing_review",
)
"""The skeleton's required keys: #25 may add keys, never drop one."""

RECORDED = {
    "separation.evidence_sha256": "1" * 64,
    "model.tokenizer_revision": C.MODEL_REVISION,
    "model.weights_sha256": "2" * 64,
    "model.license": "apache-2.0",
    "runtime.vllm_version": "0.0.0-test",
    "runtime.cuda_version": "0.0-test",
    "runtime.driver_version": "0.0-test",
    "runtime.gpu": "test GPU",
    "runtime.precision": "bfloat16",
    "runtime.max_model_len": 16_896,
    "runtime.chat_template_sha256": "3" * 64,
    "decoding.implementation": "test: response_format json_schema",
    "pilot.timing_review": {"max_atom_minutes": 19.5, "reference": ISSUE, "within_budget": True},
    "pilot.audit_sha256": {"A-pilot": "4" * 64},
}
"""Synthetic recorded values (test only; the real ones come from the GPU host and people)."""


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture(scope="module")
def current():
    return freeze.current_values()


@pytest.fixture(scope="module")
def demo_fallback():
    return load_fallback(DEMO_FALLBACK)


@pytest.fixture(scope="module")
def restricted_fallback(demo_fallback):
    """The DEMO set relabelled as a restricted-seed build (no rebuild, nothing stored)."""
    return dataclasses.replace(
        demo_fallback, demo_seed=None, seed_fingerprint=seed_fingerprint(TEST_ONLY_SEED)
    )


def _config(fallback, name="DEMO-freeze-01", llm=None, threshold="0.10") -> gc.GenerationConfig:
    meanings = load_meanings(ROOT / "generation/examples/demo-meanings")
    return gc.build_generation_config(
        name,
        llm_manifest_sha256=llm,
        decoding_schema_sha256=H,
        prompts=gc.PromptHashes("a" * 64, "b" * 64),
        meanings_sha256=meanings.sha256(),
        separation_threshold=threshold,
        fallback=gc.fallback_pins(fallback),
    )


def _recorded() -> dict[str, freeze.FreezeValue]:
    return {k: freeze.FreezeValue(v, f"test value for {k}") for k, v in RECORDED.items()}


@pytest.fixture(scope="module")
def demo_values(demo_fallback):
    config = _config(demo_fallback)
    return freeze.freeze_values(_recorded(), config, demo_fallback)


@pytest.fixture(scope="module")
def demo_manifest(demo_values):
    """A complete draft from public DEMO inputs (nothing pending)."""
    return freeze.build_freeze_manifest(demo_values)


@pytest.fixture(scope="module")
def frozen_parts(restricted_fallback):
    config = _config(restricted_fallback, name="frozen-1-0", llm=H)
    values = freeze.freeze_values(_recorded(), config, restricted_fallback)
    manifest = freeze.build_freeze_manifest(
        values, status="frozen", repo_commit=COMMIT, tag="generation-freeze-v1.0", signoff=SIGNOFF
    )
    return config, manifest


def _write(tmp_path: Path, manifest: dict, name: str = "FREEZE-v1.0.json") -> Path:
    path = tmp_path / name
    path.write_text(document_text(manifest), encoding="utf-8", newline="\n")
    return path


def _set(manifest: dict, key: str, value, *, rehash: bool = True) -> dict:
    """A copy of `manifest` with item `key` set to `value` (sha256 kept consistent)."""
    data = json.loads(json.dumps(manifest))
    for item in data["items"]:
        if item["key"] == key:
            item["value"] = value
            if rehash:
                item["sha256"] = freeze.item_sha256(freeze.SPECS[key].kind, value)
    return data


# ---------------------------------------------------------------------------
# Format


def _skeleton_draft() -> dict:
    items = [
        {
            "key": s.key,
            "category": s.category,
            "guard": s.guard,
            "value": None,
            "sha256": None,
            "path": s.path,
            "source": "pending",
        }
        for s in freeze.ITEM_SPECS
    ]
    return {
        "format": freeze.FREEZE_FORMAT,
        "format_version": 1,
        "freeze_version": "1.0",
        "status": "draft",
        "description": "DRAFT - NOT FROZEN (test)",
        "protocol_version": "0.1",
        "repo_commit": None,
        "tag": None,
        "items": items,
        "apparatus": {f: None for f in freeze.APPARATUS_FIELDS},
        "signoff": [],
    }


def test_freeze_manifest_schema():
    doc = _skeleton_draft()
    assert schema_errors("freeze-manifest.schema.json", doc) == ()
    frozen = dict(doc, status="frozen", repo_commit=COMMIT, tag="t", signoff=list(SIGNOFF))
    errors = schema_errors("freeze-manifest.schema.json", frozen)
    assert any("items" in e for e in errors) and any("apparatus" in e for e in errors)
    assert schema_errors("freeze-manifest.schema.json", dict(doc, status="frozen"))
    assert len(set(freeze.REQUIRED_ITEM_KEYS)) == len(freeze.REQUIRED_ITEM_KEYS)


def test_required_keys_hold_the_frozen_config_hash_and_fit_the_categories():
    assert FREEZE_CONFIG_KEY in freeze.REQUIRED_ITEM_KEYS
    assert set(SKELETON_KEYS) <= set(freeze.REQUIRED_ITEM_KEYS)
    assert {"meanings.sha256", "llm.manifest_sha256"} <= set(freeze.REQUIRED_ITEM_KEYS)
    schema = load_schema("freeze-manifest.schema.json")
    item = schema["properties"]["items"]["items"]["properties"]
    assert {k.split(".")[0] for k in freeze.REQUIRED_ITEM_KEYS} <= set(item["category"]["enum"])
    assert {s.guard for s in freeze.ITEM_SPECS} == set(item["guard"]["enum"])
    for spec in freeze.ITEM_SPECS:
        assert (spec.path is not None) == (spec.guard == "file"), spec.key
        expected_pending = spec.guard in ("config", "recorded") or spec.draft_pending
        assert bool(spec.pending) == expected_pending, spec.key
        assert spec.pending == "" or spec.pending.startswith("PENDING"), spec.key


def test_every_pass_criterion_of_the_issue_has_an_item():
    """Issue #25 checklist -> items (acceptance: every pass criterion has a value or hash)."""
    criteria = {
        "renderer v1.0, hash, implementation manifest": [
            "renderer.version",
            "renderer.hash",
            "renderer.implementation",
            "renderer.spec_sha256",
            "renderer.golden_manifest_sha256",
        ],
        "separation threshold from O6.2.2": ["separation.threshold", "separation.evidence_sha256"],
        "model, tokenizer, weights, vLLM, chat template, bf16, GPU": [
            "model.revision",
            "model.tokenizer_revision",
            "model.weights_sha256",
            "runtime.vllm_version",
            "runtime.chat_template_sha256",
            "runtime.precision",
            "runtime.gpu",
        ],
        "A3 and B prompt hashes and the schema hash": [
            "prompts.a3_sha256",
            "prompts.b_sha256",
            "schema.decoding_sha256",
        ],
        "decoding": [f"decoding.{k}" for k in ("temperature", "top_p", "top_k")],
        "seed function and namespaces": ["seeds.function", "seeds.namespaces"],
        "budgets": ["budget.study_a", "budget.study_b"],
        "A2 rules": ["a2.rules"],
        "selector, first-atom, tie and fallback rules": ["selector.rules", "generation.code"],
        "fallback banks and books": [
            "fallback.bank_hash",
            "fallback.banks_sha256",
            "fallback.books_sha256",
        ],
        "pilot timing review": ["pilot.timing_review", "pilot.audit_sha256"],
        "config hash": ["config.frozen_sha256", "config.document"],
    }
    for keys in criteria.values():
        assert set(keys) <= set(freeze.REQUIRED_ITEM_KEYS), keys


def test_code_values_match_the_issue_and_the_protocol(current):
    v = {k: fv.value for k, fv in current.items()}
    assert {s.key for s in freeze.ITEM_SPECS if s.guard in ("code", "file")} == set(v)
    assert (v["decoding.temperature"], v["decoding.top_p"], v["decoding.top_k"]) == (0.7, 0.9, 50)
    assert (v["decoding.repetition_penalty"], v["decoding.max_tokens"]) == (1.0, 512)
    assert v["decoding.max_input_tokens"] == 16_384
    a, b = v["budget.study_a"], v["budget.study_b"]
    assert (a["rounds_per_atom"], a["slots_per_round"], a["slot_cap_ms"]) == (4, 3, 40_000)
    assert (a["rating_slot_ms"], a["atom_budget_ms"]) == (20_000, 20 * 60_000)
    assert (b["slots_per_cell"], b["slots_per_attempt"], b["max_attempts"]) == (12, 576, 4)
    assert v["a2.rules"]["pitch_steps"] == [-3, -2, -1, 1, 2, 3]
    assert v["a2.rules"]["restarts"] == "none"
    assert v["selector.rules"]["first_atom_distinguishability"] == 4
    assert v["separation.threshold"] == "0.10"
    assert v["model.id"] == "Qwen/Qwen2.5-7B-Instruct"
    assert canonical_sha256(v["renderer.implementation"]) == v["renderer.hash"]
    assert len(freeze.reference_seed_keys()) == 2_880
    assert v["renderer.golden_manifest_sha256"] == file_sha256(ROOT / "tests/golden/manifest.json")
    assert list(v["generation.code"]) == list(freeze.GENERATION_CODE_MODULES)
    assert all(len(d) == 64 for d in v["generation.code"].values())


def test_item_hash_rule(demo_manifest):
    for item in demo_manifest["items"]:
        value, digest = item["value"], item["sha256"]
        if freeze.SPECS[item["key"]].kind == "sha256":
            assert digest == value
        elif isinstance(value, dict | list):
            assert digest == canonical_sha256(value)
        else:
            assert digest is None
    by_key = {i["key"]: i for i in demo_manifest["items"]}
    assert by_key["config.document"]["sha256"] == by_key[FREEZE_CONFIG_KEY]["value"]


def test_apparatus_fields(demo_manifest, demo_values):
    config = freeze.frozen_config(demo_manifest)
    app = demo_manifest["apparatus"]
    assert set(app) == set(freeze.APPARATUS_FIELDS)
    assert app["prompt_hash"] == canonical_sha256({"a3_sha256": "a" * 64, "b_sha256": "b" * 64})
    assert app["prompt_hash"] == canonical_sha256(dataclasses.asdict(config.prompts))
    assert app["fallback_bank_hash"] == config.fallback.bank_hash
    assert app["model_revision"] == C.MODEL_REVISION
    assert app["runtime_precision"] == "bfloat16"
    assert app["renderer_recipe_schema_hash"] == demo_values["renderer.recipe_schema_hash"].value


# ---------------------------------------------------------------------------
# The committed draft and the CI freeze guard


def test_committed_draft_is_labelled_not_frozen():
    manifest = json.loads(DRAFT.read_text(encoding="utf-8"))
    assert freeze.manifest_problems(manifest) == []
    assert manifest["status"] == "draft"
    assert manifest["description"].startswith("DRAFT - NOT FROZEN")
    assert manifest["repo_commit"] is None and manifest["tag"] is None
    assert manifest["signoff"] == []
    values = freeze.item_values(manifest)
    assert [i["key"] for i in manifest["items"]] == list(freeze.REQUIRED_ITEM_KEYS)
    for key in [k for k in values if k.startswith("runtime.")] + [FREEZE_CONFIG_KEY]:
        item = next(i for i in manifest["items"] if i["key"] == key)
        assert item["value"] is None and item["source"].startswith("PENDING"), key
    for spec in freeze.ITEM_SPECS:
        if spec.guard in ("code", "file"):
            assert (values[spec.key] is None) == spec.draft_pending, spec.key
    with pytest.raises(freeze.FreezeError) as err:
        freeze.load_freeze_manifest(DRAFT, require_frozen=True)
    assert err.value.code == freeze.E_STATUS


def test_ci_freeze_guard(current):
    """THE CI freeze guard: the active manifest equals the repository (every OS)."""
    path = freeze.active_manifest_path()
    assert path.name in (freeze.DRAFT_NAME, "FREEZE-v1.0.json") or path.name.startswith("FREEZE-v")
    differences = freeze.freeze_differences(path, current)
    assert differences == [], (
        f"{path.name} differs from the repository (a frozen value changed); after G4 this "
        "needs a new freeze version. Before G4 refresh the draft with "
        "`uv run --project generation python -m av_generation.freeze refresh`:\n"
        + "\n".join(differences)
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if path.name != freeze.DRAFT_NAME:  # pragma: no cover - from G4 on
        assert manifest["status"] == "frozen"
        assert not DRAFT.exists(), "delete the draft once the frozen manifest is committed"


def test_committed_draft_is_up_to_date():
    if not DRAFT.exists():  # pragma: no cover - from G4 on
        pytest.skip("the draft was replaced by the frozen manifest")
    text = DRAFT.read_text(encoding="utf-8")
    refreshed = document_text(freeze.refresh_draft(json.loads(text)))
    assert refreshed == text, "run `python -m av_generation.freeze refresh`"
    assert freeze.main(["refresh", "--check"]) == 0


def test_draft_cannot_start_a_confirmatory_run(frozen_parts):
    config, _ = frozen_parts
    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    with pytest.raises(gc.ConfigMismatch) as err:
        gc.check_run_config(config, kind=RunKind.CONFIRMATORY, freeze_manifest=draft)
    assert err.value.code == gc.E_FREEZE_STATUS


def test_guard_passes_on_an_unchanged_manifest(tmp_path, current, demo_manifest, frozen_parts):
    _, frozen = frozen_parts
    for manifest in (demo_manifest, frozen):
        assert freeze.freeze_differences(_write(tmp_path, manifest), current) == []
    assert freeze.freeze_differences(DRAFT, current) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("decoding.temperature", 0.8),
        ("decoding.top_k", 40),
        ("decoding.repetition_penalty", 1),
        ("renderer.hash", H),
        ("renderer.version", "1.0.0"),
        ("separation.threshold", "0.12"),
        ("validator.hash", H),
        ("seeds.function", "other-v1"),
        ("model.revision", "f" * 40),
        ("renderer.golden_manifest_sha256", H),
        ("generation.code", {"a2.py": H}),
    ],
)
def test_guard_fails_on_a_deliberately_changed_value(tmp_path, current, frozen_parts, key, value):
    _, frozen = frozen_parts
    for manifest in (frozen, json.loads(DRAFT.read_text(encoding="utf-8"))):
        path = _write(tmp_path, _set(manifest, key, value))
        differences = freeze.freeze_differences(path, current)
        assert any(d.startswith(f"{key}: frozen") for d in differences), differences
        assert freeze.main(["check", str(path)]) == 1


def test_guard_fails_when_the_code_changes(tmp_path, monkeypatch, frozen_parts):
    _, frozen = frozen_parts
    path = _write(tmp_path, frozen)
    changed = dataclasses.replace(C.FROZEN_DECODING, temperature=0.75)
    monkeypatch.setattr(C, "FROZEN_DECODING", changed)
    differences = freeze.freeze_differences(path, freeze.current_values())
    assert "decoding.temperature: frozen 0.7, current 0.75" in differences
    assert "config.document: decoding differs from the running code" in differences
    monkeypatch.setattr(C, "A2_PITCH_STEPS", (-2, -1, 1, 2))
    differences = freeze.freeze_differences(path, freeze.current_values())
    assert any(d.startswith("a2.rules: frozen") for d in differences)


def test_guard_reports_a_changed_committed_file(tmp_path, current, frozen_parts):
    """A file item is recomputed from the file: a changed threshold file fails the guard."""
    _, frozen = frozen_parts
    root = tmp_path / "repo"
    for rel in (
        freeze.GOLDEN_MANIFEST_PATH,
        freeze.RENDERER_SPEC_PATH,
        freeze.VALIDATOR_CONFIG_PATH,
        freeze.RESERVED_REGISTRY_PATH,
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, root / rel)
    assert {k: v.value for k, v in freeze.current_values(root).items()} == {
        k: v.value for k, v in current.items()
    }
    config = json.loads((root / freeze.VALIDATOR_CONFIG_PATH).read_text("utf-8"))
    config["separation_threshold"] = "0.15"
    (root / freeze.VALIDATOR_CONFIG_PATH).write_text(json.dumps(config), "utf-8")
    differences = freeze.freeze_differences(_write(tmp_path, frozen), freeze.current_values(root))
    assert differences == ['separation.threshold: frozen "0.10", current "0.15"']
    (root / freeze.RENDERER_SPEC_PATH).unlink()
    with pytest.raises(freeze.FreezeError, match="renderer-spec.md not found"):
        freeze.current_values(root)


def test_guard_reports_tampered_manifests(tmp_path, current, frozen_parts):
    config, frozen = frozen_parts
    cases = {
        "config hash": _set(frozen, FREEZE_CONFIG_KEY, H),
        "config document": _set(
            frozen, "config.document", dict(config.to_dict(), separation_threshold="0.11")
        ),
        "stale sha256": _set(frozen, "runtime.chat_template_sha256", "9" * 64, rehash=False),
        "apparatus": dict(frozen, apparatus=dict(frozen["apparatus"], prompt_hash=H)),
        "pending": _set(frozen, "runtime.gpu", None),
        "no advisor": dict(frozen, signoff=[SIGNOFF[0], SIGNOFF[0]]),
        "duplicate": dict(frozen, items=frozen["items"] + frozen["items"][:1]),
        "missing": dict(frozen, items=frozen["items"][1:]),
    }
    expected = {
        "config hash": f"{FREEZE_CONFIG_KEY}: ",
        "config document": "separation.threshold: ",
        "stale sha256": "runtime.chat_template_sha256: sha256",
        "apparatus": "apparatus.prompt_hash",
        "pending": "runtime.gpu: pending in a frozen manifest",
        "no advisor": "signoff: no advisor sign-off",
        "duplicate": "config.frozen_sha256: listed twice",
        "missing": "config.frozen_sha256: missing",
    }
    for name, manifest in cases.items():
        differences = freeze.freeze_differences(_write(tmp_path, manifest), current)
        assert any(d.startswith(expected[name]) for d in differences), (name, differences)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert "cannot read" in freeze.freeze_differences(bad, current)[0]
    bad.write_text("[]", encoding="utf-8")
    assert "not a JSON object" in freeze.freeze_differences(bad, current)[0]
    unknown = {**current, "renderer.extra": freeze.FreezeValue(1, "x")}
    assert "renderer.extra: not in the manifest" in freeze.freeze_differences(
        _write(tmp_path, frozen), unknown
    )


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    key=st.sampled_from(
        [
            s.key
            for s in freeze.ITEM_SPECS
            if s.guard == "code" and not s.key.startswith("renderer.")
        ]
    ),
    replacement=st.one_of(
        st.integers(-(2**31), 2**31),
        st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=20),
        st.dictionaries(st.sampled_from(["a", "b"]), st.integers(0, 3), max_size=2),
    ),
)
def test_property_any_changed_code_value_is_reported(
    tmp_path, current, frozen_parts, key, replacement
):
    _, frozen = frozen_parts
    if freeze.item_values(frozen)[key] == replacement:
        return
    path = _write(tmp_path, _set(frozen, key, replacement))
    differences = freeze.freeze_differences(path, current)
    assert any(d.startswith(f"{key}: frozen") for d in differences)


@settings(max_examples=25, deadline=None)
@given(
    st.dictionaries(
        st.sampled_from(sorted(RECORDED)),
        st.sampled_from(["1" * 64, "5" * 64]),
        max_size=4,
    )
)
def test_property_build_write_read_is_stable(changes):
    """Any filled draft round-trips through its file and passes the guard unchanged."""
    values = freeze.draft_values()
    for key, digest in changes.items():
        kind = freeze.SPECS[key].kind
        sample = {"sha256": digest, "sha256_map": {"x": digest}, "revision": digest[:40]}
        if kind in sample:
            values[key] = freeze.FreezeValue(sample[kind], "test")
    manifest = freeze.build_freeze_manifest(values)
    text = document_text(manifest)
    assert document_text(json.loads(text)) == text
    assert freeze.manifest_problems(json.loads(text)) == []


# ---------------------------------------------------------------------------
# Building


def test_build_requires_every_key_and_only_known_keys(demo_values):
    values = dict(demo_values)
    values.pop("runtime.gpu")
    with pytest.raises(freeze.FreezeError) as err:
        freeze.build_freeze_manifest(values)
    assert err.value.code == freeze.E_ITEM and "runtime.gpu: missing" in err.value.problems
    with pytest.raises(freeze.FreezeError, match="unknown item"):
        freeze.build_freeze_manifest({**demo_values, "runtime.extra": freeze.FreezeValue(1, "x")})
    with pytest.raises(freeze.FreezeError, match="expected a FreezeValue"):
        freeze.build_freeze_manifest({**demo_values, "runtime.gpu": "bare"})
    as_dicts = {k: {"value": v.value, "source": v.source} for k, v in demo_values.items()}
    assert freeze.build_freeze_manifest(as_dicts) == freeze.build_freeze_manifest(demo_values)


def test_frozen_manifests_need_everything(demo_values, frozen_parts, restricted_fallback):
    config, frozen = frozen_parts
    assert frozen["status"] == "frozen" and frozen["description"] == freeze.FROZEN_DESCRIPTION
    cases = [
        ({"runtime.gpu": freeze.FreezeValue(None, "PENDING")}, {}, "runtime.gpu: pending"),
        ({}, {"signoff": SIGNOFF[:1]}, "signoff: no advisor sign-off"),
        ({}, {"repo_commit": None}, "schema: repo_commit"),
    ]
    values = freeze.freeze_values(_recorded(), config, restricted_fallback)
    for change, header, message in cases:
        kwargs = {"repo_commit": COMMIT, "tag": "t", "signoff": SIGNOFF, **header}
        with pytest.raises(freeze.FreezeError) as err:
            freeze.build_freeze_manifest({**values, **change}, status="frozen", **kwargs)
        assert any(p.startswith(message) for p in err.value.problems), err.value.problems
    with pytest.raises(freeze.FreezeError) as err:
        freeze.build_freeze_manifest(
            demo_values, status="frozen", repo_commit=COMMIT, tag="t", signoff=SIGNOFF
        )
    assert "config.document: a DEMO config cannot be frozen" in err.value.problems


def test_freeze_values_check_the_fallback_and_llm_manifest(
    tmp_path, demo_fallback, restricted_fallback
):
    config = _config(demo_fallback)
    with pytest.raises(freeze.FreezeError) as err:
        freeze.freeze_values(_recorded(), config, restricted_fallback)
    assert err.value.code == freeze.E_FALLBACK
    other = _config(demo_fallback, threshold="0.11")
    with pytest.raises(freeze.FreezeError, match="another threshold"):
        freeze.fallback_values(demo_fallback, other)
    root = tmp_path / "repo"
    for rel in (
        freeze.GOLDEN_MANIFEST_PATH,
        freeze.RENDERER_SPEC_PATH,
        freeze.VALIDATOR_CONFIG_PATH,
        freeze.RESERVED_REGISTRY_PATH,
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, root / rel)
    llm = root / freeze.LLM_MANIFEST_PATH
    llm.parent.mkdir(parents=True)
    llm.write_bytes(b'{"model": "test"}\n')
    matching = _config(demo_fallback, name="DEMO-freeze-02", llm=file_sha256(llm))
    assert freeze.freeze_values(_recorded(), matching, demo_fallback, root=root)
    with pytest.raises(freeze.FreezeError) as err:
        freeze.freeze_values(_recorded(), _config(demo_fallback, llm=H), demo_fallback, root=root)
    assert err.value.code == freeze.E_CONFIG


def test_config_items_need_the_config_document(demo_manifest):
    manifest = _set(demo_manifest, "config.document", None)
    problems = freeze.manifest_problems(manifest)
    assert f"{FREEZE_CONFIG_KEY}: set without config.document" in problems
    broken = _set(demo_manifest, "config.document", {"format": "nope"})
    assert any(
        p.startswith("config.document: not a valid") for p in freeze.manifest_problems(broken)
    )
    listed = _set(demo_manifest, "config.document", [1])
    assert any("expected an object" in p for p in freeze.manifest_problems(listed))
    with pytest.raises(freeze.FreezeError) as err:
        freeze.frozen_config(broken)
    assert err.value.code == freeze.E_CONFIG
    with pytest.raises(freeze.FreezeError) as err:
        freeze.frozen_config(json.loads(DRAFT.read_text(encoding="utf-8")))
    assert err.value.code == freeze.E_PENDING


def test_items_must_match_their_spec(demo_manifest):
    data = json.loads(json.dumps(demo_manifest))
    item = next(i for i in data["items"] if i["key"] == "runtime.max_model_len")
    item.update(category="model", guard="code", path="x", value="16896", sha256=None)
    data["items"].append(dict(item, key="runtime.extra"))
    problems = freeze.manifest_problems(data)
    for text in ("category 'model'", "guard 'code'", "path 'x'", "expected an integer"):
        assert any(text in p for p in problems), text
    assert "runtime.extra: not a freeze item" in problems
    shapes = {
        "budget.study_a": [1],
        "fallback.books_sha256": {"P1": "x"},
        "model.revision": "abc",
        "runtime.gpu": "",
        "separation.threshold": "ten",
        "decoding.temperature": float("nan"),
        "renderer.hash": "ABC",
    }
    for key, value in shapes.items():
        problems = freeze.manifest_problems(_set(demo_manifest, key, value))
        assert any(p.startswith(f"{key}: expected") for p in problems), (key, problems)
    assert any(
        p.startswith("separation.threshold: expected")
        for p in freeze.manifest_problems(_set(demo_manifest, "separation.threshold", 0.1))
    )


# ---------------------------------------------------------------------------
# Confirmatory runs


def test_confirmatory_runs_refuse_a_config_that_differs(tmp_path, frozen_parts):
    config, frozen = frozen_parts
    loaded = freeze.load_freeze_manifest(_write(tmp_path, frozen), require_frozen=True)
    assert loaded.sha256 == file_sha256(tmp_path / "FREEZE-v1.0.json")
    assert freeze.frozen_config(loaded.manifest) == config
    gc.check_run_config(config, kind=RunKind.CONFIRMATORY, freeze_manifest=loaded.manifest)
    changed = dataclasses.replace(config, separation_threshold="0.11")
    with pytest.raises(gc.ConfigMismatch) as err:
        gc.check_run_config(changed, kind=RunKind.CONFIRMATORY, freeze_manifest=loaded.manifest)
    assert err.value.code == gc.E_FREEZE_MISMATCH
    with pytest.raises(freeze.FreezeError) as err:
        freeze.load_freeze_manifest(_write(tmp_path, _set(frozen, "runtime.gpu", None), "x.json"))
    assert err.value.code == freeze.E_MANIFEST
    with pytest.raises(freeze.FreezeError) as err:
        freeze.load_freeze_manifest(tmp_path / "missing.json")
    assert err.value.code == freeze.E_INPUT
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    with pytest.raises(freeze.FreezeError) as err:
        freeze.load_freeze_manifest(tmp_path / "list.json")
    assert err.value.code == freeze.E_MANIFEST


def test_active_manifest_prefers_the_newest_frozen_file(tmp_path):
    generation = tmp_path / "generation"
    generation.mkdir()
    assert freeze.active_manifest_path(tmp_path) == generation / freeze.DRAFT_NAME
    for name in ("FREEZE-v1.0.json", "FREEZE-v1.10.json", "FREEZE-v1.2.json", "FREEZE-vx.json"):
        (generation / name).write_text("{}", encoding="utf-8")
    assert freeze.active_manifest_path(tmp_path).name == "FREEZE-v1.10.json"


# ---------------------------------------------------------------------------
# Fallback re-render check (acceptance: the frozen renderer reproduces the books)


def test_rerendered_fallback_books_reproduce_their_hashes(demo_manifest, demo_fallback):
    assert freeze.verify_fallback_hashes(demo_manifest, DEMO_FALLBACK) == []
    assert freeze.verify_fallback_hashes(demo_manifest, demo_fallback) == []
    books = freeze.item_values(demo_manifest)["fallback.books_sha256"]
    assert books == {b.profile.value: b.book_sha256 for b in demo_fallback.books}


def test_fallback_check_reports_differences(tmp_path, demo_manifest, demo_fallback):
    wrong_book = _set(demo_manifest, "fallback.books_sha256", {"P1": H, "P2": H, "P3": H})
    problems = freeze.verify_fallback_hashes(wrong_book, demo_fallback)
    assert any(p.startswith("fallback.books_sha256: the fallback set gives") for p in problems)
    pending = _set(demo_manifest, "fallback.banks_sha256", None)
    assert "fallback.banks_sha256: pending in the manifest (nothing to compare)" in (
        freeze.verify_fallback_hashes(pending, demo_fallback)
    )
    other = _set(demo_manifest, "separation.threshold", "0.2")
    assert any(
        "another threshold" in p for p in freeze.verify_fallback_hashes(other, demo_fallback)
    )
    missing = _set(demo_manifest, "separation.threshold", None)
    assert "separation.threshold: missing or not decimal text" in freeze.verify_fallback_hashes(
        missing, demo_fallback
    )
    data = json.loads(DEMO_FALLBACK.read_text(encoding="utf-8"))
    data["profiles"][0]["book"]["atoms"][0]["pcm_sha256"] = H
    stale = dataclasses.replace(
        demo_fallback,
        books=(
            dataclasses.replace(
                demo_fallback.books[0],
                atoms=(
                    dataclasses.replace(demo_fallback.books[0].atoms[0], pcm_sha256=H),
                    *demo_fallback.books[0].atoms[1:],
                ),
            ),
            *demo_fallback.books[1:],
        ),
    )
    problems = freeze.verify_fallback_hashes(demo_manifest, stale)
    assert any("re-render gives another waveform" in p for p in problems)
    (tmp_path / "broken.json").write_text(json.dumps(data), encoding="utf-8")
    problems = freeze.verify_fallback_hashes(demo_manifest, tmp_path / "broken.json")
    assert problems and problems[0].startswith("fallback: cannot load")


# ---------------------------------------------------------------------------
# Drafts and the command line


def test_record_and_refresh_a_draft(tmp_path):
    path = tmp_path / freeze.DRAFT_NAME
    path.write_text(DRAFT.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    assert (
        freeze.main(
            [
                "record",
                "runtime.max_model_len",
                "--value",
                "16896",
                "--source",
                "test",
                "--manifest",
                str(path),
            ]
        )
        == 0
    )
    assert (
        freeze.main(
            [
                "record",
                "runtime.gpu",
                "--text",
                "test GPU",
                "--source",
                "test",
                "--manifest",
                str(path),
            ]
        )
        == 0
    )
    values = freeze.item_values(json.loads(path.read_text("utf-8")))
    assert values["runtime.max_model_len"] == 16_896 and values["runtime.gpu"] == "test GPU"
    assert freeze.main(["record", "runtime.gpu", "--pending", "--manifest", str(path)]) == 0
    assert freeze.item_values(json.loads(path.read_text("utf-8")))["runtime.gpu"] is None
    assert freeze.main(["refresh", str(path), "--check"]) == 0
    assert freeze.main(["refresh", str(path)]) == 0
    stale = _set(json.loads(path.read_text("utf-8")), "decoding.top_k", 1)
    _write(tmp_path, stale, freeze.DRAFT_NAME)
    assert freeze.main(["refresh", str(path), "--check"]) == 1
    assert freeze.main(["refresh", str(path)]) == 0
    assert freeze.item_values(json.loads(path.read_text("utf-8")))["decoding.top_k"] == 50
    errors = [
        ["record", "decoding.top_k", "--value", "1", "--source", "x"],
        ["record", "runtime.gpu", "--value", "{bad", "--source", "x"],
        ["record", "runtime.max_model_len", "--value", '"big"', "--source", "x"],
        ["record", "runtime.gpu", "--text", "test", "--source", " "],
    ]
    for argv in errors:
        assert freeze.main([*argv, "--manifest", str(path)]) == 2


def test_frozen_manifests_are_never_edited(frozen_parts):
    _, frozen = frozen_parts
    with pytest.raises(freeze.FreezeError) as err:
        freeze.refresh_draft(frozen)
    assert err.value.code == freeze.E_STATUS
    with pytest.raises(freeze.FreezeError):
        freeze.record_value(frozen, "runtime.gpu", "x", "y")
    with pytest.raises(freeze.FreezeError):
        freeze.pending_value("decoding.top_k")
    with pytest.raises(freeze.FreezeError):
        freeze.draft_values({"decoding.top_k": freeze.FreezeValue(1, "x")})


def test_cli_build_verify_config_and_table(tmp_path, capsys, restricted_fallback):
    fallback = tmp_path / "fallback-manifest.json"
    fallback.write_text(document_text(restricted_fallback.manifest()), "utf-8", newline="\n")
    config = _config(restricted_fallback, name="frozen-1-0", llm=H)
    config_path = tmp_path / gc.GENERATION_CONFIG_NAME
    config.write(config_path)
    draft = tmp_path / "draft.json"
    filled = json.loads(DRAFT.read_text(encoding="utf-8"))
    for key, value in RECORDED.items():
        filled = freeze.record_value(filled, key, value, f"test value for {key}")
    _write(tmp_path, filled, "draft.json")
    out = tmp_path / "FREEZE-v1.0.json"
    argv = [
        "build",
        "--draft",
        str(draft),
        "--config",
        str(config_path),
        "--fallback",
        str(fallback),
        "--out",
        str(out),
        "--status",
        "frozen",
        "--repo-commit",
        COMMIT,
        "--tag",
        "generation-freeze-v1.0",
    ]
    for entry in SIGNOFF:
        argv += ["--signoff", entry["role"], entry["date"], entry["reference"]]
    assert freeze.main(argv) == 0
    assert "fallback re-rendered" in capsys.readouterr().out
    assert freeze.main(argv) == 2  # never overwritten
    assert "never overwritten" in capsys.readouterr().err
    manifest = json.loads(out.read_text("utf-8"))
    assert manifest["status"] == "frozen" and freeze.manifest_problems(manifest) == []
    assert freeze.item_values(manifest)[FREEZE_CONFIG_KEY] == config.frozen_sha256()
    assert freeze.main(["check", str(out)]) == 0
    assert "freeze guard OK" in capsys.readouterr().out
    assert freeze.main(["verify", str(out), "--fallback", str(fallback)]) == 0
    assert "book P1" in capsys.readouterr().out
    assert freeze.main(["verify", str(out), "--fallback", str(DEMO_FALLBACK)]) == 1
    assert "FALLBACK CHECK FAILED" in capsys.readouterr().out
    extracted = tmp_path / "out" / gc.GENERATION_CONFIG_NAME
    extracted.parent.mkdir()
    assert freeze.main(["config", str(out), "--out", str(extracted)]) == 0
    assert gc.GenerationConfig.read(extracted).frozen_sha256() == config.frozen_sha256()
    assert freeze.main(["config", str(out), "--out", str(extracted)]) == 2
    capsys.readouterr()
    assert freeze.main(["table", str(out)]) == 0
    table = capsys.readouterr().out
    assert "| `config.frozen_sha256` |" in table and "| Apparatus field | Value |" in table
    assert freeze.main(["table"]) == 0
    assert "PENDING" in capsys.readouterr().out
    # a draft build from public DEMO inputs works; a frozen one is refused
    demo = _config(load_fallback(DEMO_FALLBACK))
    demo.write(tmp_path / "demo-config.json")
    base = [
        "build",
        "--draft",
        str(DRAFT),
        "--config",
        str(tmp_path / "demo-config.json"),
        "--fallback",
        str(DEMO_FALLBACK),
    ]
    assert freeze.main([*base, "--out", str(tmp_path / "demo.json")]) == 0
    assert "pending (" in capsys.readouterr().out
    assert freeze.main([*base, "--out", str(tmp_path / "x.json"), "--status", "frozen"]) == 2
    assert "DEMO fallback set cannot be frozen" in capsys.readouterr().err
    bad = ["build", "--draft", str(DRAFT), "--out", str(tmp_path / "y.json")]
    assert freeze.main([*bad, "--config", str(fallback), "--fallback", str(fallback)]) == 2
    assert freeze.main([*bad, "--config", str(config_path), "--fallback", str(draft)]) == 2
    assert freeze.main(["table", str(tmp_path / "missing.json")]) == 2
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    assert freeze.main(["table", str(tmp_path / "list.json")]) == 2


def test_cli_check_and_weights(tmp_path, capsys):
    assert freeze.main(["check"]) == 0
    assert "freeze guard OK" in capsys.readouterr().out
    model = tmp_path / "model"
    model.mkdir()
    assert freeze.main(["weights", str(model)]) == 2
    files = {"model-00001-of-00002.safetensors": b"one", "model-00002-of-00002.safetensors": b"two"}
    for name, blob in files.items():
        (model / name).write_bytes(blob)
    (model / "config.json").write_bytes(b"{}")
    assert freeze.main(["weights", str(model)]) == 0
    expected = file_set_sha256({n: file_sha256(model / n) for n in files})
    assert capsys.readouterr().out.strip() == expected == freeze.weights_sha256(model)


def test_new_draft_from_the_repository():
    fresh = freeze.draft_manifest({"runtime.gpu": freeze.FreezeValue("test GPU", "test")})
    committed = freeze.item_values(json.loads(DRAFT.read_text(encoding="utf-8")))
    values = freeze.item_values(fresh)
    assert values["runtime.gpu"] == "test GPU" and values["model.license"] is None
    for spec in freeze.ITEM_SPECS:
        if spec.guard in ("code", "file") and not spec.draft_pending:
            assert values[spec.key] == committed[spec.key], spec.key
    assert fresh["description"] == freeze.DRAFT_DESCRIPTION
