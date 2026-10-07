"""Every module of the module map imports, and the interface names owners fill exist."""

import importlib
import inspect
import pkgutil

import pytest

import av_generation

MODULES = {
    # shared contracts implemented in the skeleton
    "constants",
    "ids",
    "seeds",
    "outcomes",
    "domain",
    "jsonio",
    "records",
    "config",
    "proposers",
    "rater_protocol",
    "rundir",
    "masking",
    "clock",
    "netguard",
    "webserve",
    "llm_fake",
    "meanings",
    "genconfig",
    "panel_session",
    "_paths",
    "_schemas",
    # interfaces filled by their owner issues
    "llm",
    "ledger",
    "prompts",
    "parser",
    "a3",
    "a2",
    "a1",
    "orchestrator",
    "selector",
    "panel",
    "rater",
    "dryrun",
    "threshold",
    "audit",
    "freeze",
    "bank_manifest",
}

INTERFACES = {
    "llm": ["LlmClient", "RawOutcome", "OpenAICompatibleClient", "ChatMessage", "TokenCountError"],
    "ledger": [
        "SlotLedger",
        "SlotTicket",
        "SlotCapExceeded",
        "SlotReused",
        "SlotNotReserved",
        "AttemptCapExceeded",
    ],
    "prompts": ["PromptSet", "BuiltPrompt", "load_prompt_set", "build_a3_prompt", "build_b_prompt"],
    "parser": ["ParsedOutput", "parse_output"],
    "a3": ["A3Proposer"],
    "a2": ["A2Proposer", "sample_uniform", "mutate_pitch", "mutate_index", "mutate"],
    "a1": ["A1SlotService", "create_a1_app", "ROUTES"],
    "orchestrator": [
        "Orchestrator",
        "RatingSlotPlan",
        "panel_order_schedule",
        "panel_aliases",
    ],
    "selector": ["score_candidate", "pick_incumbent"],
    "panel": ["create_panel_app", "STATION_STATIC_DIR"],
    "rater": ["BotRater", "BotRatingPolicy"],
    "dryrun": ["check_log_completeness", "CompletenessReport", "DryRunPlan", "InjectedFallback"],
    "threshold": ["generate_stimuli", "plan_session", "summarize", "export_csv", "DEFAULT_CONFIG"],
    "audit": [
        "build_audit",
        "build_set_audit",
        "BOOK_COLUMNS",
        "MASKED_BOOK_COLUMNS",
        "METHOD_COLUMNS",
        "SET_AUDIT_NAME",
    ],
    "freeze": ["build_freeze_manifest", "freeze_differences", "REQUIRED_ITEM_KEYS"],
    "bank_manifest": ["bank_manifest_errors", "bank_sha256"],
}


def test_module_set():
    found = {m.name for m in pkgutil.iter_modules(av_generation.__path__)}
    assert found >= MODULES


@pytest.mark.parametrize("name", sorted(INTERFACES))
def test_interface_names(name):
    module = importlib.import_module(f"av_generation.{name}")
    for attr in INTERFACES[name]:
        assert hasattr(module, attr), f"{name}.{attr}"


def test_round_proposers_share_the_protocol_shape():
    from av_generation.a1 import A1SlotService
    from av_generation.a2 import A2Proposer
    from av_generation.a3 import A3Proposer
    from av_generation.proposers import RoundProposer

    for cls in (A1SlotService, A2Proposer, A3Proposer):
        assert cls.method.value in ("A1", "A2", "A3")
        sig = inspect.signature(cls.propose_round)
        assert list(sig.parameters) == ["self", "request"]
    assert "propose_round" in dir(RoundProposer)
