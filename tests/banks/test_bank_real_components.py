"""The builder with #17's real ledger, B prompt builder and parser.

#16 and #17 are built in parallel with #26; until their modules are filled these tests
skip (the skeleton raises `NotImplementedError`). After the generation stack is stacked
(#17 before #26) they run the same oracle comparison as the scripted tests.
"""

import pytest
from av_generation.clock import ManualClock
from av_generation.ledger import SlotLedger
from av_generation.llm_fake import ScriptedLlmClient
from av_generation.parser import parse_output
from av_generation.prompts import build_b_prompt
from av_generation.proposers import BCellState
from av_sound.recipe import Profile

from av_banks.builder import BankBuilder
from av_banks.proposer import LlmSlotProposer
from av_banks.verify import verify_bank


def _available(tmp_path, kit):
    try:
        SlotLedger(tmp_path / "probe.jsonl", run_id="DEMO-probe", clock=ManualClock())
        parse_output("{}")
        cell = BCellState("DEMO-bank-01", 1, Profile.P1, "K-a1", 1, "FLIP_CARD", (), ())
        build_b_prompt(cell, prompt_set=kit.prompt_set)
    except NotImplementedError as err:
        pytest.skip(f"#17 module not filled yet: {err}")


def _ledger(path, *, run_id, clock, refusals, timing, cap):
    return SlotLedger(path, run_id=run_id, clock=clock, refusals=refusals, timing=timing, cap=cap)


def test_real_ledger_prompt_builder_and_parser(kit, tmp_path):
    _available(tmp_path, kit)
    order = kit.permutation.atom_order

    def kind(attempt, profile, atom, slot):
        if attempt == 1 and (profile, atom) == ("P1", order[3]):
            return "invalid_json"
        return ("valid", "repeat", "near", "timeout", "schema")[(slot * 7 + len(atom)) % 5]

    script = kit.script(kind)
    client = ScriptedLlmClient([script.respond] * 2400)
    proposer = LlmSlotProposer(client, kit.prompt_set, kit.decoding_schema)
    builder = BankBuilder(
        kit.spec(),
        tmp_path / "out" / "DEMO-bank-01",
        config=kit.config,
        proposer=proposer,
        clock=ManualClock(),
        run_id="DEMO-run-real",
        ledger_factory=_ledger,
        fsync=False,
    )
    result = builder.build()
    expected = kit.expected_bank(script, order)
    assert [a.status for a in result.attempts] == [e["status"] for e in expected]
    assert [a.slots_used for a in result.attempts] == [e["slots"] for e in expected]
    assert verify_bank(result.bank_dir).ok
