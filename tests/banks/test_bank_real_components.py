"""The builder with #17's real ledger, B prompt builder, parser and committed prompt set.

#16 and #17 are built in parallel with #26; until #17's modules are filled this test
skips (the skeleton raises `NotImplementedError`). After the generation stack is stacked
(#17 before #26) it runs the same oracle comparison as the scripted tests.
"""

import pytest
from av_generation import prompts
from av_generation.clock import ManualClock
from av_generation.ledger import SlotLedger
from av_generation.llm_fake import ScriptedLlmClient
from av_generation.parser import parse_output
from av_generation.proposers import BCellState
from av_sound.recipe import Profile

from av_banks.builder import BankBuilder
from av_banks.proposer import LlmSlotProposer
from av_banks.verify import verify_bank


def _committed_prompt_set(kit, tmp_path):
    """#17's committed prompt set, or a skip while #17 is not filled."""
    try:
        SlotLedger(tmp_path / "probe.jsonl", run_id="DEMO-probe", clock=ManualClock())
        parse_output("{}")
        prompt_set = prompts.load_prompt_set(
            prompts.default_prompt_set_dir(),
            meanings=kit.meanings,
        )
        cell = BCellState("DEMO-bank-01", 1, Profile.P1, "K-a1", 1, "FLIP_CARD", (), ())
        prompts.build_b_prompt(cell, prompt_set=prompt_set)
    except (NotImplementedError, AttributeError) as err:
        pytest.skip(f"#17 module not filled yet: {err}")
    return prompt_set


def _ledger(path, *, run_id, clock, refusals, timing, cap):
    return SlotLedger(path, run_id=run_id, clock=clock, refusals=refusals, timing=timing, cap=cap)


def test_real_ledger_prompt_builder_and_parser(kit, tmp_path):
    prompt_set = _committed_prompt_set(kit, tmp_path)
    config = kit.make_config(prompt_set=prompt_set)
    order = kit.permutation.atom_order

    def kind(attempt, profile, atom, slot):
        if attempt == 1 and (profile, atom) == ("P1", order[3]):
            return "invalid_json"
        return ("valid", "repeat", "near", "timeout", "schema")[(slot * 7 + len(atom)) % 5]

    script = kit.script(kind)
    client = ScriptedLlmClient([script.respond] * 2400)
    proposer = LlmSlotProposer(
        client, prompt_set, kit.decoding_schema, threshold=config.separation_threshold
    )
    builder = BankBuilder(
        kit.spec(),
        tmp_path / "out" / "DEMO-bank-01",
        config=config,
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
    # the real B prompt carries the retained prefix and no rating field
    last = client.calls[-1].messages[-1]["content"]
    assert '"retained"' in last and "rating" not in last
