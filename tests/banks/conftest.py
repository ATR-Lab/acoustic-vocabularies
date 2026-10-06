"""Bank-builder tests need the `av_banks` package (banks/ uv project) and its dev group.

Run them with `uv run --project banks pytest --import-mode=importlib tests/banks`. A bare
`python -m pytest` from the repository root (without the package) skips this directory.

The ledger (#17), the B-mode prompt builder and parser (#17) and the model client (#16)
are built in parallel with #26, so the tests use scripted stand-ins with the same
contracts (shared through the `kit` fixture; test modules cannot import this file):

- `MemoryLedger`: `SlotLedger`'s documented contract (reserve checks the cap and the
  slot ID before any work and logs refusals; consume needs the open ticket; records go
  to the attempt's `slots.jsonl` through `RecordWriter`);
- `dump_prompt`: a B prompt that serializes the whole `BCellState` it is given (so the
  sentinel test sees everything the proposer could see) plus the meaning text;
- `strict_parser`: exactly one strict JSON object, else `invalid_json`;
- `BankScript`: a scripted fake model. Every slot's output is a pure function of
  (attempt, profile, atom, slot) and a "kind" (valid, repeat, copy, near, invalid,
  timeout, ...); the model is `llm_fake.ScriptedLlmClient` and token counts come from
  the same script (`overflow_input`, failed counts);
- `expected_bank`: an independent oracle of the Study B §4 retention rule over the same
  script (technical validity, distinct waveforms within the cell, compatibility with
  every retained option of another atom under the profile).

Every test runs under `av_generation.netguard.deny_outbound()`.
"""

import importlib.util

if importlib.util.find_spec("av_banks") is None or importlib.util.find_spec("hypothesis") is None:
    collect_ignore_glob = ["*"]
else:
    import dataclasses
    import hashlib
    import json
    import random
    import threading
    from collections import Counter
    from dataclasses import dataclass, field
    from pathlib import Path
    from types import SimpleNamespace

    import pytest
    from av_generation import genconfig as gc
    from av_generation.clock import ManualClock
    from av_generation.constants import (
        B_MAX_ATTEMPTS,
        B_OPTIONS_PER_CELL,
        B_SLOTS_PER_CELL,
        PROFILES,
    )
    from av_generation.jsonio import file_set_sha256, messages_sha256, schema_sha256, to_json_value
    from av_generation.ledger import SlotCapExceeded, SlotNotReserved, SlotReused, SlotTicket
    from av_generation.llm import TokenCountError
    from av_generation.llm_fake import (
        ScriptedLlmClient,
        overflow_outcome,
        server_error_outcome,
        timeout_outcome,
    )
    from av_generation.meanings import load_meanings
    from av_generation.netguard import deny_outbound
    from av_generation.parser import ParsedOutput
    from av_generation.prompts import BuiltPrompt, PromptSet
    from av_generation.records import RecordWriter, SlotRecord, SlotRefusal
    from av_sound import load_fallback
    from av_sound._paths import schema_path as sound_schema_path
    from av_sound.features import N_FEATURES, features, parse_threshold, sum_squared_diff
    from av_sound.recipe import (
        AMPLITUDES,
        GAPS_MS,
        PITCHES,
        RHYTHM_WEIGHTS,
        TOTAL_MS,
        StrictJsonError,
        strict_json_loads,
    )
    from av_sound.renderer import MIN_EVENT_SAMPLES, event_samples
    from av_sound.validate import validate

    from av_banks.builder import BankBuilder, bank_spec
    from av_banks.permutation import load_permutation
    from av_banks.proposer import LlmSlotProposer, b_prompt_sha256

    ROOT = Path(__file__).resolve().parents[2]
    FIXTURES = Path(__file__).resolve().parent / "fixtures"
    DEMO_UNIT = FIXTURES / "demo-unit-B-C01" / "permutation.json"
    THRESHOLD = "0.10"

    @pytest.fixture(autouse=True)
    def _no_outbound_network():
        with deny_outbound() as refused:
            yield refused
        assert not refused, f"outbound network attempts: {refused}"

    # -- #17 stand-ins ------------------------------------------------------

    class MemoryLedger:
        """`SlotLedger` contract (#17) for one attempt's `slots.jsonl`."""

        def __init__(self, path, *, run_id, clock, refusals=None, timing=None, cap=12):
            self.path = Path(path)
            self.run_id = run_id
            self.clock = clock
            self.cap = cap
            self._writer = RecordWriter(self.path, types=(SlotRecord,), fsync=False)
            self._refusals = refusals
            self._lock = threading.Lock()
            self._used = Counter()
            self._open = {}
            self._seen = set()
            self._records = []

        def _refuse(self, cap_key, slot_id, reason, study, method):
            if self._refusals is not None:
                self._refusals.append(
                    SlotRefusal(
                        run_id=self.run_id,
                        study=study,
                        method=method,
                        cap_key=cap_key,
                        reason=reason,
                        requested=slot_id,
                        used=self._used[cap_key],
                        t_ms=self.clock.now_ms(),
                    )
                )

        def reserve(self, cap_key, slot_id, *, study, method):
            with self._lock:
                if slot_id in self._seen:
                    self._refuse(cap_key, slot_id, "slot_reused", study, method)
                    raise SlotReused(slot_id)
                if self._used[cap_key] >= self.cap:
                    self._refuse(cap_key, slot_id, "slot_cap", study, method)
                    raise SlotCapExceeded(cap_key)
                self._used[cap_key] += 1
                self._seen.add(slot_id)
                ticket = SlotTicket(
                    cap_key, slot_id, self._used[cap_key], study, method, self.clock.now_ms()
                )
                self._open[slot_id] = ticket
                return ticket

        def consume(self, record):
            with self._lock:
                ticket = self._open.pop(record.slot_id, None)
                if (
                    ticket is None
                    or ticket.cap_key != record.cap_key
                    or ticket.slot_index != record.slot_index
                ):
                    raise SlotNotReserved(record.slot_id)
                self._writer.append(record)
                self._records.append(record)
                return record

        def used(self, cap_key):
            return self._used[cap_key]

        def remaining(self, cap_key):
            return self.cap - self._used[cap_key]

        def records(self, cap_key=None):
            return tuple(r for r in self._records if cap_key is None or r.cap_key == cap_key)

    class UncappedLedger(MemoryLedger):
        """A ledger without a cap: shows that the builder enforces 12 slots itself."""

        def __init__(self, path, **kwargs):
            kwargs["cap"] = 10**6
            super().__init__(path, **kwargs)

    def dump_prompt(cell, *, prompt_set, threshold=None):
        """A B prompt holding everything in the `BCellState` (and the meaning text)."""
        context = to_json_value(cell)
        context["meaning"] = prompt_set.meanings.text(cell.semantic_label)
        context["threshold"] = threshold
        text = json.dumps(context, sort_keys=True, separators=(",", ":"))
        messages = (
            {"role": "system", "content": prompt_set.b_instruction},
            {"role": "user", "content": text},
        )
        return BuiltPrompt(messages, messages_sha256(messages), text)

    def strict_parser(text):
        if text is None:
            return ParsedOutput(None, "no text")
        try:
            obj = strict_json_loads(text.encode("utf-8"))
        except StrictJsonError as err:
            return ParsedOutput(None, str(err))
        if not isinstance(obj, dict):
            return ParsedOutput(None, "not a JSON object")
        return ParsedOutput(obj, None)

    # -- the scripted model -------------------------------------------------

    TEXT_KINDS = (
        "valid",
        "repeat",
        "copy",
        "near",
        "invalid_json",
        "two_objects",
        "schema",
        "domain",
        "short",
    )
    STATUS_KINDS = ("timeout", "overflow_output", "server_error")
    COUNT_KINDS = ("overflow_input", "token_error")
    KINDS = TEXT_KINDS + STATUS_KINDS + COUNT_KINDS
    SHORT = (
        '{"total_ms":450,"pitches":[0,0,0],"rhythm_weights":[1,4,4],'
        '"gaps_ms":[60,60],"amplitudes":[1.0,0.8,0.6]}'
    )

    def _rng(*parts):
        digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    def fresh_recipe(tag, attempt, profile, atom, slot):
        """A random recipe whose events are long enough (deterministic per key)."""
        rng = _rng("fresh", tag, attempt, profile, atom, slot)
        while True:
            recipe = {
                "total_ms": rng.choice(TOTAL_MS),
                "pitches": [rng.choice(PITCHES) for _ in range(3)],
                "rhythm_weights": [rng.choice(RHYTHM_WEIGHTS) for _ in range(3)],
                "gaps_ms": [rng.choice(GAPS_MS) for _ in range(2)],
                "amplitudes": [rng.choice(AMPLITUDES) for _ in range(3)],
            }
            n = event_samples(recipe["total_ms"], recipe["rhythm_weights"], recipe["gaps_ms"])
            if min(n) >= MIN_EVENT_SAMPLES:
                return recipe

    @dataclass
    class BankScript:
        """Scripted model outputs for one bank: `kind(attempt, profile, atom, slot)`."""

        atom_order: tuple
        kind: object
        tag: str = "s"
        calls: list = field(default_factory=list)

        def prev_atom(self, atom):
            i = self.atom_order.index(atom)
            return self.atom_order[i - 1] if i else None

        def output(self, attempt, profile, atom, slot):
            """`("forced", kind)` or `("text", text)` for one slot."""
            kind = self.kind(attempt, profile, atom, slot)
            if kind in STATUS_KINDS or kind in COUNT_KINDS:
                return ("forced", kind)
            return ("text", self.text(kind, attempt, profile, atom, slot))

        def text(self, kind, attempt, profile, atom, slot):
            prev = self.prev_atom(atom)
            if kind == "repeat" and slot > 1:
                out = self.output(attempt, profile, atom, slot - 1)
                return out[1] if out[0] == "text" else "{"
            if kind in ("copy", "near") and prev is not None:
                recipe = fresh_recipe(self.tag, attempt, profile, prev, 1)
                if kind == "near":
                    p = recipe["pitches"][0]
                    recipe["pitches"][0] = p + 1 if p < 6 else p - 1
                return json.dumps(recipe)
            if kind == "invalid_json":
                return "not json {"
            if kind == "two_objects":
                return '{"a": 1} {"b": 2}'
            if kind == "schema":
                return '{"total_ms": 600}'
            if kind == "domain":
                recipe = fresh_recipe(self.tag, attempt, profile, atom, slot)
                recipe["total_ms"] = 500
                return json.dumps(recipe)
            if kind == "short":
                return SHORT
            return json.dumps(fresh_recipe(self.tag, attempt, profile, atom, slot))

        def _key(self, seed_key):
            _, _ns, attempt, profile, atom, slot = seed_key.split("|")
            return int(attempt), profile, atom, int(slot)

        def respond(self, messages, schema, seed_key):
            key = self._key(seed_key)
            self.calls.append(key)
            kind, value = self.output(*key)
            if kind == "text":
                return value
            return {
                "timeout": timeout_outcome(),
                "overflow_output": overflow_outcome(),
                "server_error": server_error_outcome(),
            }[value]

        def count_tokens(self, messages):
            context = json.loads(messages[-1]["content"])
            key = (context["attempt"], context["profile"], context["atom_id"], context["slot"])
            kind = self.kind(*key)
            if kind == "overflow_input":
                return 20_000
            if kind == "token_error":
                raise TokenCountError("scripted /tokenize failure")
            return 100

        def client(self):
            return ScriptedLlmClient(
                [self.respond] * (B_MAX_ATTEMPTS * 576 + 10), token_counter=self.count_tokens
            )

    def all_kind(kind):
        return lambda attempt, profile, atom, slot: kind

    def mixed_kinds(seed, p_valid):
        """Each slot: `valid` with probability `p_valid`, else any other kind."""
        others = [k for k in KINDS if k != "valid"]

        def kind(attempt, profile, atom, slot):
            rng = _rng("kind", seed, attempt, profile, atom, slot)
            return "valid" if rng.random() < p_valid else rng.choice(others)

        return kind

    # -- the oracle ---------------------------------------------------------

    def expected_bank(script, atom_order, threshold=THRESHOLD):
        """The Study B §4 rule applied independently to `script` (sequential traversal).

        Returns a list of attempts: `{"status", "failed_cell", "slots", "cells"}` with
        `cells[(profile, atom)] = [(slot, recipe_dict, pcm_sha256), ...]`.
        """
        limit = N_FEATURES * parse_threshold(threshold) ** 2
        attempts = []
        for attempt in range(1, B_MAX_ATTEMPTS + 1):
            cells = {}
            slots = 0
            failed = None
            for profile in PROFILES:
                others = []  # (features, pcm) of retained options of earlier atoms
                for atom in atom_order:
                    cell = []
                    used = 0
                    for slot in range(1, B_SLOTS_PER_CELL + 1):
                        if len(cell) == B_OPTIONS_PER_CELL:
                            break
                        used += 1
                        kind, text = script.output(attempt, profile, atom, slot)
                        if kind == "forced":
                            continue
                        obj = strict_parser(text).obj
                        if obj is None:
                            continue
                        result = validate(obj, profile, (), threshold=threshold)
                        if not result.ok:
                            continue
                        pcm = result.pcm_sha256
                        if any(pcm == c[2] for c in cell):
                            continue
                        f = features(result.recipe)
                        if any(pcm == o[1] or sum_squared_diff(f, o[0]) < limit for o in others):
                            continue
                        cell.append((slot, result.recipe.to_dict(), pcm, f))
                    slots += used
                    cells[(profile, atom)] = [c[:3] for c in cell]
                    if len(cell) < B_OPTIONS_PER_CELL:
                        failed = (profile, atom)
                        break
                    others.extend((c[3], c[2]) for c in cell)
                if failed:
                    break
            attempts.append(
                {
                    "status": "failed" if failed else "complete",
                    "failed_cell": failed,
                    "slots": slots,
                    "cells": cells,
                }
            )
            if not failed:
                break
        return attempts

    # -- configs, proposer, builders ----------------------------------------

    def _decoding_schema():
        return json.loads(sound_schema_path("recipe.schema.json").read_text(encoding="utf-8"))

    def make_prompt_set(meanings, instruction="DEMO B instruction (synthetic, not a study text)"):
        """A synthetic B prompt set. Fields #17 adds to `PromptSet` (sections, per-mode
        hashes) are filled when the class has them, so the kit works before and after #17."""
        files = {"b/instruction.txt": hashlib.sha256(instruction.encode()).hexdigest()}
        values = {
            "name": "DEMO-prompts-b",
            "demo": True,
            "a3_instruction": "DEMO A3 instruction (synthetic)",
            "b_instruction": instruction,
            "meanings": meanings,
            "files": files,
            "set_sha256": file_set_sha256(files),
            "a3_sections": {},
            "b_sections": {},
            "a3_sha256": file_set_sha256({"a3/instruction.txt": "a" * 64}),
            "b_sha256": file_set_sha256(files),
            "schema_sha256": schema_sha256(_decoding_schema()),
        }
        names = {f.name for f in dataclasses.fields(PromptSet)}
        return PromptSet(**{k: v for k, v in values.items() if k in names})

    def make_config(name="DEMO-gen-banks", *, prompt_set, llm=None, threshold=THRESHOLD):
        fallback = load_fallback(ROOT / "sound/testvectors/fallback/demo-manifest.json")
        return gc.build_generation_config(
            name,
            llm_manifest_sha256=llm,
            decoding_schema_sha256=schema_sha256(_decoding_schema()),
            prompts=gc.PromptHashes("a" * 64, b_prompt_sha256(prompt_set)),
            meanings_sha256=prompt_set.meanings.sha256(),
            separation_threshold=threshold,
            fallback=gc.fallback_pins(fallback),
        )

    class Kit(SimpleNamespace):
        """Helpers shared by the bank tests (see the module docstring)."""

        def script(self, kind, tag="s", atom_order=None):
            return BankScript(atom_order or self.permutation.atom_order, kind, tag)

        def proposer(self, script):
            return LlmSlotProposer(
                script.client(),
                self.prompt_set,
                self.decoding_schema,
                prompt_builder=dump_prompt,
                parser=strict_parser,
                threshold=self.config.separation_threshold,
            )

        def spec(self, bank_id="DEMO-bank-01", permutation=None, **kwargs):
            return bank_spec(bank_id, permutation or self.permutation, **kwargs)

        def builder(
            self,
            root,
            script,
            *,
            bank_id="DEMO-bank-01",
            spec=None,
            config=None,
            ledger=MemoryLedger,
            clock=None,
            **kwargs,
        ):
            spec = spec or self.spec(bank_id)
            return BankBuilder(
                spec,
                Path(root) / spec.bank_id,
                config=config or self.config,
                proposer=self.proposer(script),
                clock=clock or ManualClock(),
                run_id=kwargs.pop("run_id", "DEMO-run-banks"),
                ledger_factory=ledger,
                fsync=False,
                **kwargs,
            )

        def build(self, root, script, **kwargs):
            return self.builder(root, script, **kwargs).build()

    @pytest.fixture(scope="session")
    def kit():
        meanings = load_meanings(ROOT / "generation/examples/demo-meanings")
        prompt_set = make_prompt_set(meanings)
        return Kit(
            root=ROOT,
            permutation=load_permutation(DEMO_UNIT),
            meanings=meanings,
            prompt_set=prompt_set,
            decoding_schema=_decoding_schema(),
            config=make_config(prompt_set=prompt_set),
            make_config=make_config,
            make_prompt_set=make_prompt_set,
            Ledger=MemoryLedger,
            UncappedLedger=UncappedLedger,
            dump_prompt=dump_prompt,
            strict_parser=strict_parser,
            all_kind=all_kind,
            mixed_kinds=mixed_kinds,
            expected_bank=expected_bank,
            fresh_recipe=fresh_recipe,
            KINDS=KINDS,
            DEMO_UNIT=DEMO_UNIT,
            THRESHOLD=THRESHOLD,
        )

    @pytest.fixture(scope="session")
    def built_bank(kit, tmp_path_factory):
        """One complete DEMO bank (all-valid script), built once for read-only tests."""
        root = tmp_path_factory.mktemp("built")
        result = kit.build(root, kit.script(all_kind("valid")))
        return result
