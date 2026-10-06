"""Prompt set and A3/B prompt builders (#17): fixed instruction, hashes, determinism, leak
tests and the worst-case prompt."""

import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from av_sound.features import FEATURE_NAMES, features
from av_sound.grammar import ATOM_IDS
from av_sound.recipe import Profile
from av_sound.synthetic import synthetic_recipes
from av_sound.validate import REASON_CODES
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation import _demo_ledger as demo
from av_generation import _prompt_budget as budget
from av_generation.genconfig import FallbackPins, PromptHashes, build_generation_config
from av_generation.ids import Method, Study, bank_slot_id, proposal_slot_id, slot_index
from av_generation.jsonio import messages_sha256, schema_sha256, write_document
from av_generation.outcomes import SlotOutcome
from av_generation.prompts import (
    DYNAMIC_SECTIONS,
    MANIFEST_NAME,
    STATIC_SECTIONS,
    PromptContextError,
    PromptSetError,
    _decimal,
    build_a3_prompt,
    build_b_prompt,
    default_prompt_set_dir,
    load_prompt_set,
    prompt_set_manifest,
)
from av_generation.proposers import (
    AtomFeedback,
    BCellState,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RaterScore,
    RetainedOption,
)
from av_generation.records import SlotRecord

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "generation" / "prompts"

INSTRUCTION_SHA256 = "05738403a734776ffa77729d142b26ec36dbc58767a29cf161bd377ca904ebe3"
"""SHA-256 of the fixed instruction of Study A protocol §3.6 (the text between the quotation
marks, 219 ASCII bytes, no final newline), computed from the protocol document."""
INSTRUCTION_BYTES = 219
PINNED_SET = {
    "a3_sha256": "0e20949a8ff628ba3258a06b70eb5231342466799301d4d5c67a22c9fd681209",
    "b_sha256": "c23dab615f354ccb471c5c980842f7285b1b031ae85723138841df3a8579a8a7",
    "set_sha256": "d3f6c84f8247dd810f520038611bcad45cf82c9ad9ac4ed75754ac2613d0e4ce",
}
"""Prompt-set hashes (frozen at G4): a change here is a new prompt version."""
PINNED_DEMO_A3_PROMPT = "80d350c91982ebf2f6aeebb0bd1a54860de644cab83e0f920470c9a4ce0dd861"
PINNED_DEMO_B_PROMPT = "bf0ed2d9c2068c9c9897985f44bea6999875d2a934b22013fd6b55724ddf7243"
PINNED_WORST_CASE = {
    "a3": ("f8915d6a94499ae8919d4e7b567cba4c500a904bcb9279f267f32ea8405bf575", 14650),
    "a3_padded": ("f4ef43231c7ba788bb0efa9bcc4035befffcfc28d310abf9807256eb618dc6f6", 15244),
    "b": ("43241d841c2de9d09efdd3c679d011818764c14e17df98177010e4f3951ce2c4", 20786),
}
"""Worst-case prompts whose token counts are recorded in generation/docs/prompts-and-ledger.md
(4,923, 5,085 and 10,115 tokens with the pinned tokenizer): if a hash changes, recount."""

RATING_KEYS = {"ratings", "association", "distinguishability", "comfort", "score", "eligible"}
FORBIDDEN_KEYS = {
    "run_id",
    "batch_id",
    "book_id",
    "bank_id",
    "slot_id",
    "seed",
    "seed_key",
    "designer_id",
    "rater_id",
    "participant_id",
    "station",
    "raw_output",
    "method",
    "prompt_sha256",
    "pcm_sha256",
}


@pytest.fixture(scope="module")
def prompt_set():
    return demo.demo_prompt_set()


def context_of(prompt):
    return json.loads(prompt.messages[1]["content"])


def keys_of(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from keys_of(value)
    elif isinstance(node, list):
        for value in node:
            yield from keys_of(value)


def values_of(node, key):
    if isinstance(node, dict):
        for k, value in node.items():
            if k == key:
                yield value
            yield from values_of(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from values_of(value, key)


# ---------------------------------------------------------------------------
# The fixed instruction and the prompt set


def test_stored_instruction_matches_study_a_byte_for_byte(prompt_set):
    for mode in ("a3", "b"):
        raw = (PROMPTS / mode / "instruction.txt").read_bytes()
        assert len(raw) == INSTRUCTION_BYTES
        assert hashlib.sha256(raw).hexdigest() == INSTRUCTION_SHA256
    assert hashlib.sha256(prompt_set.a3_instruction.encode("ascii")).hexdigest() == (
        INSTRUCTION_SHA256
    )
    assert prompt_set.b_instruction == prompt_set.a3_instruction


def test_prompts_send_the_instruction_unchanged(prompt_set):
    a3 = build_demo_a3(prompt_set)
    b = build_b_prompt(demo.demo_cell(1), prompt_set=prompt_set, threshold="0.10")
    for prompt in (a3, b):
        system, user = prompt.messages
        assert system == {"role": "system", "content": prompt_set.a3_instruction}
        assert user == {"role": "user", "content": prompt.context_json}
        assert prompt.prompt_sha256 == messages_sha256(prompt.messages)


def test_hash_file_matches_the_files_and_is_pinned(prompt_set):
    stored = json.loads((PROMPTS / MANIFEST_NAME).read_text("utf-8"))
    assert stored == prompt_set_manifest(PROMPTS, name=stored["name"])
    assert {k: stored[k] for k in PINNED_SET} == PINNED_SET
    assert stored["files"]["a3/instruction.txt"] == INSTRUCTION_SHA256
    assert stored["demo"] is False and prompt_set.demo is False
    assert prompt_set.set_sha256 == PINNED_SET["set_sha256"]
    assert prompt_set.hashes() == PromptHashes(PINNED_SET["a3_sha256"], PINNED_SET["b_sha256"])
    assert default_prompt_set_dir() == PROMPTS


def test_template_schema_is_the_published_recipe_schema(prompt_set):
    sound = json.loads((ROOT / "sound" / "schema" / "recipe.schema.json").read_text("utf-8"))
    expected = {
        k: v for k, v in sound.items() if k not in ("$schema", "$id", "title", "description")
    }
    for sections in (prompt_set.a3_sections, prompt_set.b_sections):
        assert sections["schema"] == expected
        assert sections["features"]["order"] == list(FEATURE_NAMES)
        assert set(sections["codes"]) == set(REASON_CODES)
        assert set(sections) == STATIC_SECTIONS
    assert prompt_set.schema_sha256 == schema_sha256(expected)


def test_prompt_hashes_feed_the_generation_config(prompt_set):
    config = build_generation_config(
        "DEMO-config-01",
        llm_manifest_sha256=None,
        decoding_schema_sha256=schema_sha256(demo.demo_decoding_schema()),
        prompts=prompt_set.hashes(),
        meanings_sha256=prompt_set.meanings.sha256(),
        separation_threshold="0.10",
        fallback=FallbackPins("0" * 64, {p: "0" * 64 for p in ("P1", "P2", "P3")}),
    )
    assert config.prompts == prompt_set.hashes()
    load_prompt_set(PROMPTS, meanings=prompt_set.meanings, expected=config.prompts)
    with pytest.raises(PromptSetError, match="expected"):
        load_prompt_set(
            PROMPTS, meanings=prompt_set.meanings, expected=PromptHashes("0" * 64, "0" * 64)
        )


def _copy(tmp_path):
    target = tmp_path / "prompts"
    shutil.copytree(PROMPTS, target)
    return target


def _rehash(path, name="prompts-v1"):
    (path / MANIFEST_NAME).unlink()
    write_document(path / MANIFEST_NAME, prompt_set_manifest(path, name=name))


def test_edited_files_are_refused(tmp_path, prompt_set):
    meanings = prompt_set.meanings
    target = _copy(tmp_path)
    (target / "a3" / "instruction.txt").write_bytes(
        (PROMPTS / "a3" / "instruction.txt").read_bytes().replace(b"one JSON", b"a JSON")
    )
    with pytest.raises(PromptSetError, match="do not match"):
        load_prompt_set(target, meanings=meanings)
    _rehash(target)  # a re-hashed edit loads, with other hashes than the frozen set
    assert load_prompt_set(target, meanings=meanings).a3_sha256 != PINNED_SET["a3_sha256"]


@pytest.mark.parametrize(
    "edit",
    [
        "extra_file",
        "missing_manifest",
        "manifest_keys",
        "trailing_newline",
        "non_ascii",
        "sections",
        "template_format",
        "template_mode",
        "template_json",
        "schema_differs",
        "schema_type",
        "name",
    ],
)
def test_malformed_sets_are_refused(tmp_path, prompt_set, edit):
    target = _copy(tmp_path)
    manifest_path = target / MANIFEST_NAME
    a3_template = target / "a3" / "context-template.json"
    doc = json.loads(a3_template.read_text("utf-8"))
    template_edits = {
        "sections": lambda d: d["sections"].update(task={}),
        "template_format": lambda d: d.update(format_version=2),
        "template_mode": lambda d: d.update(mode="B"),
        "schema_differs": lambda d: d["sections"]["schema"].update(title="other"),
        "schema_type": lambda d: d["sections"].update(schema=[]),
    }
    if edit in template_edits:
        template_edits[edit](doc)
        a3_template.write_text(json.dumps(doc))
        with pytest.raises(PromptSetError):
            prompt_set_manifest(target, name="prompts-v1")
    elif edit == "template_json":
        a3_template.write_text("{")
    elif edit in ("trailing_newline", "non_ascii"):
        path = target / "b" / "instruction.txt"
        raw = path.read_bytes()
        path.write_bytes(raw + b"\n" if edit == "trailing_newline" else "\u00f6".encode() + raw)
        _rehash(target)
    elif edit == "extra_file":
        (target / "a3" / "notes.txt").write_text("x")
    elif edit == "missing_manifest":
        manifest_path.unlink()
    else:
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest.update({"extra": 1} if edit == "manifest_keys" else {"name": ""})
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(PromptSetError):
        load_prompt_set(target, meanings=prompt_set.meanings)


# ---------------------------------------------------------------------------
# A3 prompts


def demo_a3_args(prompt_set, *, round_=2, slot=2):
    """A DEMO state: round 1 closed with one rated, one invalid and one rated candidate;
    slot 1 of round 2 already proposed."""
    recipes = synthetic_recipes(Profile.P1)
    book = demo.demo_book_state()
    sid = lambda r, s: proposal_slot_id(demo.BOOK, demo.A_ATOM, r, s)  # noqa: E731
    ratings = (
        RaterScore(6, 3, "acceptable"),
        RaterScore(2, 5, None),
        RaterScore(6, 3, "unacceptable"),
    )
    candidates = (
        CandidateFeedback(
            sid(1, 3),
            1,
            3,
            3,
            recipes["K-r3"],
            SlotOutcome.VALID,
            (),
            ratings,
            True,
            Fraction(25, 6),
        ),
        CandidateFeedback(
            sid(1, 1),
            1,
            1,
            1,
            recipes["K-r1"],
            SlotOutcome.VALID,
            (),
            ratings[:2],
            True,
            Fraction(9, 2),
        ),
        CandidateFeedback(
            sid(1, 2), 1, 2, 2, None, SlotOutcome.OUT_OF_DOMAIN, ("E_DOMAIN",), (), False, None
        ),
    )
    feedback = AtomFeedback(demo.BOOK, demo.A_ATOM, 1, candidates, sid(1, 1), Fraction(9, 2))
    earlier = SlotRecord(
        run_id="DEMO-run-01",
        study=Study.A,
        method=Method.A3,
        slot_id=sid(2, 1),
        profile=Profile.P1,
        atom_id=demo.A_ATOM,
        slot=1,
        slot_index=4,
        outcome=SlotOutcome.SEPARATION_FAIL,
        t_open_ms=0,
        t_ms=0,
        batch_id=demo.BATCH,
        book_id=demo.BOOK,
        round=2,
        recipe=recipes["K-r2"].to_dict(),
        validator_codes=("E_SEPARATION",),
        raw_output="SECRET-RAW-OUTPUT",
    )
    return (book, demo.A_ATOM, round_, slot), {
        "semantic_label": "ALIGN_ARROW",
        "feedback": feedback,
        "same_round": (earlier,),
        "prompt_set": prompt_set,
    }


def build_demo_a3(prompt_set, **kwargs):
    args, kw = demo_a3_args(prompt_set, **kwargs)
    return build_a3_prompt(*args, **kw)


def test_a3_context_layout(prompt_set):
    prompt = build_demo_a3(prompt_set)
    ctx = context_of(prompt)
    assert set(ctx) == STATIC_SECTIONS | DYNAMIC_SECTIONS["a3"]
    assert prompt.context_json == json.dumps(ctx, sort_keys=True, separators=(",", ":"))
    assert ctx["task"] == {
        "atom_id": "K-a4",
        "family": "K",
        "meaning": prompt_set.meanings.text("ALIGN_ARROW"),
        "role": "action",
        "round": 2,
        "semantic_label": "ALIGN_ARROW",
        "separation_threshold": "0.1",
        "slot": 2,
        "slot_index": 5,
        "slots_per_atom": 12,
    }
    assert ctx["profile"] == {"f0_hz": 300, "id": "P1"}
    book = demo.demo_book_state()
    assert [c["atom_id"] for c in ctx["committed"]] == list(demo.COMMITTED)
    first = ctx["committed"][0]
    assert first["recipe"] == book.committed[0].recipe.to_dict()
    assert first["features"] == [round(float(f), 4) for f in features(book.committed[0].recipe)]
    assert first["meaning"] == prompt_set.meanings.text(first["semantic_label"])
    fb = ctx["feedback"]
    assert [c["slot_index"] for c in fb["candidates"]] == [1, 2, 3]
    assert fb["incumbent_slot_index"] == 1 and fb["rounds_closed"] == 1
    third = fb["candidates"][2]
    assert third["score"] == 4.1667 and third["eligible"] is True
    assert third["ratings"] == [  # sorted, so seat order never shows
        {"association": 2, "comfort": None, "distinguishability": 5},
        {"association": 6, "comfort": "acceptable", "distinguishability": 3},
        {"association": 6, "comfort": "unacceptable", "distinguishability": 3},
    ]
    assert fb["candidates"][1] == {
        "eligible": False,
        "outcome": "out_of_domain",
        "ratings": [],
        "recipe": None,
        "round": 1,
        "score": None,
        "slot": 2,
        "slot_index": 2,
        "validator_codes": ["E_DOMAIN"],
    }
    [same] = fb["same_round"]
    assert same["slot_index"] == 4 and same["outcome"] == "separation_fail"
    assert "ratings" not in same and "SECRET-RAW-OUTPUT" not in prompt.context_json


def test_a3_prompt_is_deterministic_and_pinned(prompt_set):
    first = build_demo_a3(prompt_set)
    second = build_demo_a3(demo.demo_prompt_set())
    assert first == second
    assert first.prompt_sha256 == PINNED_DEMO_A3_PROMPT


def test_same_state_same_hash_in_fresh_processes():
    code = (
        "from av_generation import _demo_ledger as d;"
        "from av_generation.prompts import build_b_prompt;"
        "print(build_b_prompt(d.demo_cell(1), prompt_set=d.demo_prompt_set(),"
        " threshold='0.10').prompt_sha256)"
    )
    hashes = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
        )
        hashes.add(out.stdout.strip())
    assert hashes == {PINNED_DEMO_B_PROMPT}


def test_order_of_inputs_does_not_change_the_prompt(prompt_set):
    args, kw = demo_a3_args(prompt_set)
    feedback = kw["feedback"]
    reordered = AtomFeedback(
        feedback.book_id,
        feedback.atom_id,
        feedback.rounds_closed,
        tuple(reversed(feedback.candidates)),
        feedback.incumbent_slot_id,
        feedback.incumbent_score,
    )
    assert build_a3_prompt(*args, **{**kw, "feedback": reordered}) == build_a3_prompt(*args, **kw)


def test_slot_one_of_round_one_has_no_feedback(prompt_set):
    args, kw = demo_a3_args(prompt_set, round_=1, slot=1)
    ctx = context_of(build_a3_prompt(*args, **kw))
    assert ctx["feedback"] == {
        "candidates": [],
        "incumbent_slot_index": None,
        "rounds_closed": 0,
        "same_round": [],
    }


def test_first_atom_has_no_committed_entries(prompt_set):
    args, kw = demo_a3_args(prompt_set)
    book = demo.demo_book_state()
    empty = BookState(book.batch_id, book.book_id, book.profile, book.threshold, ())
    ctx = context_of(build_a3_prompt(empty, *args[1:], **kw))
    assert ctx["committed"] == []


def test_label_free_committed_atoms_have_no_meaning(prompt_set):
    args, kw = demo_a3_args(prompt_set)
    ctx = context_of(build_a3_prompt(args[0].without_labels(), *args[1:], **kw))
    assert {(c["semantic_label"], c["meaning"]) for c in ctx["committed"]} == {(None, None)}


@pytest.mark.parametrize(
    "problem", ["other_book", "other_atom", "committed", "slot", "round", "label", "atom"]
)
def test_contract_violations_raise_before_anything_is_charged(prompt_set, problem):
    args, kw = demo_a3_args(prompt_set)
    book, atom, round_, slot = args
    fb = kw["feedback"]
    if problem == "other_book":
        kw["feedback"] = AtomFeedback("DEMO-BK-7QX4", atom, 1, (), None, None)
    elif problem == "other_atom":
        kw["feedback"] = AtomFeedback(fb.book_id, "K-a1", 1, (), None, None)
    elif problem == "committed":
        atom = "K-a1"
        kw["feedback"] = AtomFeedback(fb.book_id, atom, 1, (), None, None)
    elif problem == "slot":
        slot = 4
    elif problem == "round":
        round_ = 5
    elif problem == "label":
        kw["semantic_label"] = "NOT_A_LABEL"
    elif problem == "atom":
        atom = "K-x9"
        kw["feedback"] = AtomFeedback(fb.book_id, atom, 1, (), None, None)
    with pytest.raises(PromptContextError):
        build_a3_prompt(book, atom, round_, slot, **kw)


# ---------------------------------------------------------------------------
# B prompts


def test_b_context_layout_has_no_rating_field(prompt_set):
    cell = demo.demo_cell(3)
    prompt = build_b_prompt(cell, prompt_set=prompt_set, threshold="0.10")
    ctx = context_of(prompt)
    assert set(ctx) == STATIC_SECTIONS | DYNAMIC_SECTIONS["b"]
    assert not RATING_KEYS & set(keys_of(ctx))
    assert ctx["task"] == {
        "atom_id": "K-a4",
        "family": "K",
        "meaning": prompt_set.meanings.text("ALIGN_ARROW"),
        "options_per_cell": 4,
        "options_retained": 1,
        "role": "action",
        "semantic_label": "ALIGN_ARROW",
        "separation_threshold": "0.1",
        "slot": 3,
        "slots_per_cell": 12,
    }
    assert [(o["atom_id"], o["same_atom"]) for o in ctx["retained"]] == [
        ("K-a1", False),
        ("K-a2", False),
        ("K-a4", True),
    ]
    assert ctx["feedback"] == {"candidates": []}


def test_b_history_shows_only_this_cells_earlier_slots(tmp_path, prompt_set):
    from av_generation.clock import ManualClock

    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock)
    records, _ = demo.run_b_slots([SlotOutcome.VALID, SlotOutcome.DUPLICATE], ledger, clock)
    other = SlotRecord(
        **{
            **{
                f: getattr(records[0], f)
                for f in ("run_id", "study", "method", "outcome", "t_open_ms", "t_ms", "bank_id")
            },
            "slot_id": bank_slot_id(demo.BANK, 2, "P1", demo.A_ATOM, 1),
            "profile": Profile.P1,
            "atom_id": demo.A_ATOM,
            "slot": 1,
            "slot_index": 1,
            "attempt": 2,
            "raw_output": "OTHER-ATTEMPT-SENTINEL",
        }
    )
    cell = demo.demo_cell(3, (*records, other))
    ctx = context_of(build_b_prompt(cell, prompt_set=prompt_set, threshold="0.10"))
    assert [(c["slot"], c["outcome"]) for c in ctx["feedback"]["candidates"]] == [
        (1, "valid"),
        (2, "duplicate"),
    ]
    assert ctx["feedback"]["candidates"][0]["recipe"] == records[0].recipe
    early = demo.demo_cell(2, (*records, other))
    assert (
        len(
            context_of(build_b_prompt(early, prompt_set=prompt_set, threshold="0.10"))["feedback"][
                "candidates"
            ]
        )
        == 1
    )


def test_b_threshold_defaults_to_the_validator_config(prompt_set):
    ctx = context_of(build_b_prompt(demo.demo_cell(1), prompt_set=prompt_set))
    assert ctx["task"]["separation_threshold"] == "0.1"


def test_b_contract_violations(prompt_set):
    cell = demo.demo_cell(1)
    for bad in (
        BCellState(cell.bank_id, 1, cell.profile, cell.atom_id, 13, cell.semantic_label, (), ()),
        BCellState(cell.bank_id, 1, cell.profile, cell.atom_id, 1, "NOPE", (), ()),
        BCellState(
            cell.bank_id,
            1,
            Profile.P2,
            cell.atom_id,
            1,
            cell.semantic_label,
            cell.retained,
            (),
        ),
    ):
        with pytest.raises(PromptContextError):
            build_b_prompt(bad, prompt_set=prompt_set, threshold="0.10")


# ---------------------------------------------------------------------------
# Sentinel leak test: 1,000 prompts, 0 forbidden fields

PARTICIPANT = "PX-SENTINEL-4821"
DESIGNER = "DSENT9"
SECRET = "SENTINEL-RAW-TEXT"
CURRENT_ROUND_RATING = RaterScore(811, 833, "acceptable")
OTHER_BOOK_RATING = RaterScore(913, 917, "acceptable")
OTHER_BOOK = "DEMO-BK-7QX4"


def _sentinel_record(rng, *, book, atom, round_, slot, study=Study.A, recipe=None):
    common = dict(
        run_id=f"DEMO-{PARTICIPANT}",
        study=study,
        profile=Profile.P1,
        atom_id=atom,
        slot=slot,
        slot_index=slot_index(round_, slot) if study is Study.A else slot,
        outcome=rng.choice([SlotOutcome.VALID, SlotOutcome.SEPARATION_FAIL, SlotOutcome.TIMEOUT]),
        t_open_ms=0,
        t_ms=0,
        recipe=None if recipe is None else recipe.to_dict(),
        raw_output=f"{SECRET} {PARTICIPANT}",
        seed_key=f"A3|{PARTICIPANT}|{atom}|1|1",
        designer_id=DESIGNER,
    )
    if study is Study.A:
        return SlotRecord(
            **common,
            method=Method.A3,
            slot_id=proposal_slot_id(book, atom, round_, slot),
            batch_id=f"DEMO-{PARTICIPANT}",
            book_id=book,
            round=round_,
        )
    return SlotRecord(
        **common,
        method=Method.B,
        slot_id=bank_slot_id(demo.BANK, 1, "P1", atom, slot),
        bank_id=demo.BANK,
        attempt=1,
    )


def _random_a3(rng, prompt_set, recipes):
    labels = budget.default_labels()
    atoms = list(ATOM_IDS)
    rng.shuffle(atoms)
    atom, committed = atoms[0], atoms[1 : 1 + rng.randint(0, 15)]
    book = BookState(
        f"DEMO-{PARTICIPANT}",
        demo.BOOK,
        Profile.P1,
        "0.10",
        tuple(
            CommittedAtom(a, labels[a], recipes[a], "a" * 64, i) for i, a in enumerate(committed)
        ),
    )
    round_, slot = rng.randint(1, 4), rng.randint(1, 3)
    own, leaks = [], []
    for r in range(1, round_ + 1):
        for s in (1, 2, 3):
            valid = rng.random() < 0.7
            recipe = recipes[rng.choice(atoms)] if valid else None
            if r < round_:
                ratings = (
                    tuple(
                        RaterScore(
                            rng.randint(1, 7),
                            rng.randint(1, 7),
                            rng.choice(["acceptable", "unacceptable", None]),
                        )
                        for _ in range(3)
                    )
                    if valid
                    else ()
                )
                own.append(
                    CandidateFeedback(
                        proposal_slot_id(demo.BOOK, atom, r, s),
                        r,
                        s,
                        slot_index(r, s),
                        recipe,
                        SlotOutcome.VALID if valid else SlotOutcome.INVALID_JSON,
                        () if valid else ("E_JSON",),
                        ratings,
                        valid,
                        Fraction(rng.randint(2, 14), 2) if valid else None,
                    )
                )
            else:  # the current round: its ratings must never show
                leaks.append(
                    CandidateFeedback(
                        proposal_slot_id(demo.BOOK, atom, r, s),
                        r,
                        s,
                        slot_index(r, s),
                        recipe,
                        SlotOutcome.VALID,
                        (),
                        (CURRENT_ROUND_RATING,) * 3,
                        True,
                        Fraction(8111, 1000),
                    )
                )
            leaks.append(  # another book's candidate (another method's scores)
                CandidateFeedback(
                    proposal_slot_id(OTHER_BOOK, atom, r, s),
                    r,
                    s,
                    slot_index(r, s),
                    recipe,
                    SlotOutcome.VALID,
                    (),
                    (OTHER_BOOK_RATING,) * 3,
                    True,
                    Fraction(9137, 1000),
                )
            )
    candidates = own + leaks
    rng.shuffle(candidates)
    feedback = AtomFeedback(
        demo.BOOK, atom, round_ - 1 + rng.randint(0, 1), tuple(candidates), None, None
    )
    same_round = tuple(
        _sentinel_record(
            rng, book=b, atom=atom, round_=round_, slot=s, recipe=recipes[rng.choice(atoms)]
        )
        for b in (demo.BOOK, OTHER_BOOK)
        for s in (1, 2, 3)
    )
    prompt = build_a3_prompt(
        book,
        atom,
        round_,
        slot,
        semantic_label=labels[atom],
        feedback=feedback,
        same_round=same_round,
        prompt_set=prompt_set,
    )
    expected = sum(1 for c in own if c.round < round_)
    return prompt, expected, slot - 1


def _random_b(rng, prompt_set, recipes):
    labels = budget.default_labels()
    atoms = list(ATOM_IDS)
    rng.shuffle(atoms)
    atom = atoms[0]
    retained = tuple(
        RetainedOption(
            Profile.P1,
            a,
            rank,
            recipes[rng.choice(atoms)],
            "b" * 64,
            bank_slot_id(demo.BANK, 1, "P1", a, rank),
        )
        for a in atoms[: rng.randint(0, 15)]
        for rank in (1, 2, 3, 4)
    )
    slot = rng.randint(1, 12)
    history = tuple(
        _sentinel_record(
            rng, book=demo.BOOK, atom=a, round_=1, slot=s, study=Study.B, recipe=recipes[a]
        )
        for a in (atom, atoms[1])
        for s in range(1, 13)
    )
    cell = BCellState(demo.BANK, 1, Profile.P1, atom, slot, labels[atom], retained, history)
    return build_b_prompt(cell, prompt_set=prompt_set, threshold="0.10"), slot - 1


def _findings(prompt, mode):
    ctx = context_of(prompt)
    text = "".join(m["content"] for m in prompt.messages)
    found = []
    for sentinel in (PARTICIPANT, DESIGNER, SECRET, demo.BOOK, OTHER_BOOK, demo.BANK):
        if sentinel in text:
            found.append(f"sentinel {sentinel}")
    found += [f"key {k}" for k in set(keys_of(ctx)) & FORBIDDEN_KEYS]
    if mode == "b":
        found += [
            f"rating key {k}" for k in set(keys_of(ctx)) & (RATING_KEYS | {"incumbent_slot_index"})
        ]
    dynamic = {k: v for k, v in ctx.items() if k not in STATIC_SECTIONS}
    for key in ("association", "distinguishability"):
        found += [
            f"{key} {v}" for v in values_of(dynamic, key) if v is not None and not 1 <= v <= 7
        ]
    found += [f"score {v}" for v in values_of(dynamic, "score") if v is not None and v > 7]
    return found


def test_sentinel_leaks_in_1000_prompts(prompt_set):
    recipes = synthetic_recipes(Profile.P1)
    rng = random.Random(20261005)
    findings, prompts = [], 0
    for i in range(1000):
        if i % 2 == 0:
            prompt, expected, earlier = _random_a3(rng, prompt_set, recipes)
            ctx = context_of(prompt)
            assert len(ctx["feedback"]["candidates"]) == expected
            assert len(ctx["feedback"]["same_round"]) == earlier
            findings += _findings(prompt, "a3")
        else:
            prompt, earlier = _random_b(rng, prompt_set, recipes)
            assert len(context_of(prompt)["feedback"]["candidates"]) == earlier
            findings += _findings(prompt, "b")
        prompts += 1
    assert prompts == 1000
    assert findings == []


def test_the_leak_check_detects_leaks(prompt_set):
    """The check itself works: a prompt that carries the sentinels is flagged."""
    prompt = build_demo_a3(prompt_set)
    leaked = type(prompt)(
        (
            prompt.messages[0],
            {
                "role": "user",
                "content": json.dumps(
                    {"x": {"rater_id": PARTICIPANT, "score": 9.1, "association": 811}}
                ),
            },
        ),
        prompt.prompt_sha256,
        prompt.context_json,
    )
    assert len(_findings(leaked, "b")) == 6


# ---------------------------------------------------------------------------
# Number formatting and the worst case


@settings(max_examples=300, deadline=None)
@given(st.fractions(min_value=0, max_value=10, max_denominator=10_000))
def test_decimal_rounding_is_exact_half_even(value):
    shown = _decimal(value)
    assert abs(Fraction(repr(shown)) - value) <= Fraction(1, 20_000)
    assert len(repr(shown).split(".")[1]) <= 4
    assert Fraction(repr(shown)) == Fraction(round(value * 10_000), 10_000)


def test_worst_case_prompts_are_pinned_and_below_the_limit():
    prompt_set = load_prompt_set(PROMPTS, meanings=budget.long_meanings())
    report = budget.report(prompt_set, None)
    for name, (sha, chars) in PINNED_WORST_CASE.items():
        assert report[name] == {"prompt_sha256": sha, "chars": chars}
    a3 = context_of(budget.worst_case_a3_prompt(prompt_set))
    assert len(a3["committed"]) == 15
    assert len(a3["feedback"]["candidates"]) + len(a3["feedback"]["same_round"]) == 11
    assert all(len(c["ratings"]) == 3 for c in a3["feedback"]["candidates"])
    assert {len(m) for m in prompt_set.meanings.meanings.values()} == {200}
    b = context_of(budget.worst_case_b_prompt(prompt_set))
    assert len(b["retained"]) == 63 and len(b["feedback"]["candidates"]) == 11


def _tiny_tokenizer(path):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "<|im_start|>": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.save(str(path))
    return path


def test_offline_token_count_and_command_line(tmp_path, capsys):
    tok = _tiny_tokenizer(tmp_path / "tokenizer.json")
    messages = ({"role": "system", "content": "a b"}, {"role": "user", "content": "c"})
    text = budget.chat_text(messages)
    assert text == (
        "<|im_start|>system\na b<|im_end|>\n<|im_start|>user\nc<|im_end|>\n<|im_start|>assistant\n"
    )
    assert budget.count_tokens_offline(messages, tok) > 0
    with pytest.raises(ValueError):
        budget.chat_text(messages[1:])
    assert budget.main(["--tokenizer", str(tok)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["prompt_set"] == "prompts-v1" and out["a3"]["tokens"] > 0
    assert out["tokenizer_sha256"] == hashlib.sha256(tok.read_bytes()).hexdigest()
    assert budget.main([]) == 0
    assert "tokens" not in json.loads(capsys.readouterr().out)["b"]


def test_malformed_inputs_are_dropped_not_shown(prompt_set):
    """Unparseable slot IDs, duplicate candidates and unusable recipes never reach a prompt."""
    args, kw = demo_a3_args(prompt_set)
    fb = kw["feedback"]
    junk = replace(fb.candidates[0], slot_id="garbage")
    doubled = AtomFeedback(
        fb.book_id,
        fb.atom_id,
        fb.rounds_closed,
        (*fb.candidates, fb.candidates[0], junk),
        fb.incumbent_slot_id,
        fb.incumbent_score,
    )
    [earlier] = kw["same_round"]
    broken = replace(earlier, recipe={"total_ms": 1})
    ctx = context_of(build_a3_prompt(*args, **{**kw, "feedback": doubled, "same_round": (broken,)}))
    assert [c["slot_index"] for c in ctx["feedback"]["candidates"]] == [1, 2, 3]
    assert ctx["feedback"]["same_round"][0]["recipe"] is None
    cell = demo.demo_cell(2)
    stray = replace(earlier, study=Study.B, slot_id="garbage", slot=1)
    cell = replace(cell, history=(stray,))
    ctx = context_of(build_b_prompt(cell, prompt_set=prompt_set, threshold="0.10"))
    assert ctx["feedback"]["candidates"] == []


def test_template_must_be_an_object(tmp_path, prompt_set):
    target = _copy(tmp_path)
    (target / "b" / "context-template.json").write_text("[]")
    with pytest.raises(PromptSetError, match="expected keys"):
        load_prompt_set(target, meanings=prompt_set.meanings)


def test_a_candidate_must_be_the_round_its_slot_id_names(prompt_set):
    """A current-round candidate mislabelled as round 1 is dropped, not shown with ratings."""
    args, kw = demo_a3_args(prompt_set, round_=2, slot=1)
    fb = kw["feedback"]
    current = proposal_slot_id(demo.BOOK, demo.A_ATOM, 2, 1)
    mislabelled = replace(fb.candidates[1], slot_id=current, ratings=(RaterScore(811, 811, None),))
    feedback = replace(fb, candidates=(*fb.candidates, mislabelled))
    ctx = context_of(build_a3_prompt(*args, **{**kw, "feedback": feedback, "same_round": ()}))
    assert [c["slot_index"] for c in ctx["feedback"]["candidates"]] == [1, 2, 3]
    assert "811" not in json.dumps(ctx["feedback"])
