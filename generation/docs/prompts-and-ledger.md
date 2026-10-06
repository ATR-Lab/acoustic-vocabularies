# Prompts, output parser and slot ledger (#17)

This document covers four modules and the prompt set:

- `av_generation.prompts`: the prompt set, `build_a3_prompt` and `build_b_prompt`.
- `av_generation.parser`: `parse_output`.
- `av_generation.ledger`: `SlotLedger`.
- `av_generation.a3`: `A3Proposer` and `BSlotProposer`.
- `generation/prompts/`: the prompt set.

Sources are Study A protocol §3.3 and §3.6 and Study B protocol §4. For architecture,
log contracts and outcome codes, see [`architecture.md`](architecture.md). For the API
summary, see [`docs/interfaces/generation.md`](../../docs/interfaces/generation.md).

## 1. Prompt set

`generation/prompts/` is the committed prompt set `prompts-v1`. It is frozen at G4 by
its hashes.

| File | Contents |
| --- | --- |
| `a3/instruction.txt` | The fixed instruction of Study A §3.6: 219 ASCII bytes, one line, no final newline |
| `b/instruction.txt` | The Study B instruction: the same bytes (section 7, decision 1) |
| `a3/context-template.json`, `b/context-template.json` | The static context sections of each mode (format `av-generation/context-template` v1) |
| `prompt-set.json` | The hash file: file SHA-256s, set hashes and the schema hash |

The instruction file holds the exact text between the quotation marks of Study A §3.6.
Its SHA-256 is
`05738403a734776ffa77729d142b26ec36dbc58767a29cf161bd377ca904ebe3`, and
`tests/generation/test_prompt_builder.py` pins that hash. Editors that add a final
newline break the hash test, which is intended.

The hashes use the shared definition, `jsonio.file_set_sha256` over relative POSIX
paths. Each value is pinned in the tests and goes into the freeze:

| Hash | Value | Used as |
| --- | --- | --- |
| `a3_sha256` (files under `a3/`) | `0e20949a8ff628ba3258a06b70eb5231342466799301d4d5c67a22c9fd681209` | freeze item `prompts.a3_sha256`, `GenerationConfig.prompts.a3_sha256` |
| `b_sha256` (files under `b/`) | `c23dab615f354ccb471c5c980842f7285b1b031ae85723138841df3a8579a8a7` | freeze item `prompts.b_sha256`, `GenerationConfig.prompts.b_sha256` |
| `set_sha256` (all four files) | `d3f6c84f8247dd810f520038611bcad45cf82c9ad9ac4ed75754ac2613d0e4ce` | `PromptSet.set_sha256` |
| `schema_sha256` (the recipe schema shown in both templates) | `a8ee5754442748826ebf2452a59e2d6cb7925f6db887163c4228a3cc5b957b6c` | `jsonio.schema_sha256` of the context's `schema` section |

`load_prompt_set(path, *, meanings, expected=None)` checks these points:

- The directory holds exactly the four files plus `prompt-set.json`.
- Every hash in the hash file matches the files.
- The instructions are printable ASCII on one line.
- The templates have exactly the static sections, the right mode and the same recipe
  schema.
- `expected` (`GenerationConfig.prompts`) equals the set hashes.

Any failure raises `PromptSetError`. `PromptSet.hashes()` returns the
`genconfig.PromptHashes` value for `build_generation_config`.

To change a template on purpose, edit it and regenerate the hash file:

```bash
uv run --project generation python -c "from av_generation.prompts import *; from av_generation.jsonio import write_document; d = default_prompt_set_dir(); (d / 'prompt-set.json').unlink(); write_document(d / 'prompt-set.json', prompt_set_manifest(d, name='prompts-v2'))"
```

After that, update the pinned hashes in the tests and recount the worst case (section
4). After G4, a change like this needs a new protocol version.

Meaning texts are not part of the prompt set. They come from the shared meaning set
(`av_generation.meanings`, Common procedures §3). The meaning set stays in restricted
storage; only its hash is committed, and tests use the DEMO set.

## 2. Prompt format

Every model call sends two chat messages:

1. `system`: the mode's instruction, byte for byte. Sending it as the system message
   also replaces the default system message of the pinned chat template.
2. `user`: the context block. This is one canonical JSON object: sorted keys, compact
   separators, ASCII only. `BuiltPrompt.context_json` holds it.

`BuiltPrompt.prompt_sha256` is `jsonio.messages_sha256(messages)`. The slot record and
#16's `LlmRequest` carry the same value.

Each context copies the template's static sections unchanged:

- `checks`: the admissibility checks in short phrases.
- `codes`: a glossary of the validator codes.
- `feedback_fields`: what the feedback fields mean.
- `features`: the 12-feature order, normalization and distance.
- `grammar`: action motif, 200 ms silence, referent motif; 3 events per motif.
- `schema`: the published recipe schema (`sound/schema/recipe.schema.json` without
  `$schema`, `$id`, `title` and the root `description`). A test keeps it equal to the
  published schema.

The builders then add the dynamic sections.

| Section | A3 (`build_a3_prompt`) | B (`build_b_prompt`) |
| --- | --- | --- |
| `task` | atom ID, family, role, semantic label, meaning, round, slot, slot index, `slots_per_atom`, separation threshold | atom ID, family, role, semantic label, meaning, slot, `slots_per_cell`, `options_per_cell`, `options_retained`, separation threshold |
| `profile` | `id`, `f0_hz` | `id`, `f0_hz` |
| `committed` / `retained` | the book's committed atoms in commit order: label, meaning, recipe, 12 features | every option retained so far in the attempt and profile, in order: atom, rank, recipe, 12 features, `same_atom` |
| `feedback` | `candidates` of closed rounds (slot index, round, slot, outcome, validator codes, recipe, ratings, eligibility, score), `incumbent_slot_index`, `rounds_closed`, `same_round` (earlier proposals of the current round, no ratings) | `candidates`: this cell's earlier slots (slot, outcome, validator codes, recipe) |

Numbers are exact integers, or recipe amplitudes (0.6, 0.8, 1.0). Features and scores
are exact fractions, rounded half-even to 4 decimals, so `1/3` appears as `0.3333`. The
separation threshold is a decimal string such as `"0.1"`. Ratings are sorted, so the
order of the panel seats never shows.

The same state gives a byte-identical prompt and hash, on every platform and with any
`PYTHONHASHSEED`. The tests pin one A3 and one B prompt hash, and CI runs them on Linux,
macOS and Windows.

## 3. Masking and leak rules

The context never holds any of these:

- run, batch, book, bank, slot or rater IDs;
- seeds or seed keys;
- designer or participant data;
- raw model text;
- prompt or waveform hashes;
- another book's scores.

The builders filter their inputs; they do not trust the caller.

- **A3 feedback.** The feedback must be this book's and this atom's (else
  `PromptContextError`). Only candidates whose slot ID names this book, this atom and
  the candidate's own round and slot are shown, and only from rounds that have closed:
  rounds up to
  `min(feedback.rounds_closed, round - 1)`. Ratings of the current round never reach
  the prompt.
- **A3 same-round proposals.** Only this book's earlier slots of the current round and
  atom are shown, without ratings.
- **B.** Only this cell's earlier slots (same bank, attempt, profile and atom) are shown.
  No rating field exists in B mode.
- **Labels.** A label-free book (`BookState.without_labels()`) gives committed entries
  without label or meaning.

`test_prompt_builder.py::test_sentinel_leaks_in_1000_prompts` checks these rules. It
builds 500 A3 and 500 B prompts from random states that carry the following sentinels:

- participant and designer strings in run IDs, batch IDs, seed keys, designer IDs and raw
  output;
- other-book candidates with out-of-range ratings and scores;
- current-round candidates with out-of-range ratings and scores;
- B history from other cells.

The test finds 0 forbidden sentinels, keys or values. A second test shows that the
check does detect a leak.

## 4. Token budget and `overflow_input`

Before every call, A3 and B count the prompt with the server's `/tokenize`
(`LlmClient.count_prompt_tokens`, #16). That count uses the pinned tokenizer and chat
template.

- Above 16,384 tokens (`constants.MAX_INPUT_TOKENS`), the slot closes as
  `overflow_input`, with `tokens_in` set and no model call.
- A failed count (`TokenCountError`) closes the slot as `invalid_json`, with
  `llm_status="server_error"` and no call.

### Worst-case prompts

The worst cases are built by `av_generation._prompt_budget` from synthetic data. Every
meaning text has the schema maximum of 200 characters.

| Prompt | Contents | Characters | Tokens |
| --- | --- | --- | --- |
| A3 worst case (issue): `f8915d6a94499ae8919d4e7b567cba4c500a904bcb9279f267f32ea8405bf575` | slot 12 of the last atom; 15 committed atoms with the longest recipes; 9 rated candidates (3 ratings each); 2 same-round proposals with 5 validator codes each | 14,650 | **4,923** |
| A3 padded bound: `f4ef43231c7ba788bb0efa9bcc4035befffcfc28d310abf9807256eb618dc6f6` | as above, and every rated candidate also carries 5 validator codes (not a reachable state) | 15,244 | 5,085 |
| B worst case: `43241d841c2de9d09efdd3c679d011818764c14e17df98177010e4f3951ce2c4` | slot 12 of the last cell; 63 retained options (15 other atoms x 4, plus 3 of this atom); 11 earlier slots with 5 codes each | 20,786 | **10,115** |

How the counts were made:

- Tokenizer: `Qwen/Qwen2.5-7B-Instruct` at revision
  `a09a35458c702b33eeacc393d103063234e8bc28`.
- Files: `tokenizer.json`, SHA-256
  `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` (git blob
  `443909a6...`), and `tokenizer_config.json` with the chat template. Only these two
  files were downloaded, at development time; they are not in the repository.
- Template: the generation prompt was added (`add_generation_prompt=true`).
- Cross-check: three methods gave the same counts. They were
  `_prompt_budget.count_tokens_offline`, the real Jinja template with `tokenizers`, and
  `transformers.AutoTokenizer.apply_chat_template`.

All three prompts stay below 16,384 tokens; the B worst case reaches 62% of the limit.
So `overflow_input` cannot happen with this prompt set and the current schemas. The rule
stays in place, and tests check it with a scripted count.

The tests pin the prompt hashes and character counts. If a template or builder change
alters them, recount with the pinned tokenizer:

```bash
uv run --project generation python -m av_generation._prompt_budget --tokenizer <path>/tokenizer.json
```

These JSON contexts run at about 2 to 3 characters per token, so the character count of
a new worst case is a safe upper bound on its token count.

## 5. Output parser

`parse_output(text)` accepts exactly one JSON object. The text must be strict JSON
(`av_sound.recipe.strict_json_loads`), so duplicate keys, `NaN`, `Infinity` and a
byte-order mark are all refused. JSON whitespace around the object is allowed.

Everything else is `invalid_json`. Examples:

- prose before or after the object;
- a second object;
- a Markdown code fence;
- an array, string or number;
- empty text or no text.

The parser never repairs output. A3 and B log a refused text as validator code `E_JSON`,
with the parser's message (printable ASCII, at most 400 characters). So for every parsed
slot, `outcome_from_validator_codes(record.validator_codes) == record.outcome`. A parsed
object goes to `av_sound.validate` unchanged, which reports `E_SCHEMA` and `E_DOMAIN`.

## 6. Slot ledger

`SlotLedger(path, *, run_id, clock, refusals=None, timing=None, cap=12)` is the one
ledger for A1 (#19), A2 (#18), A3 and the Study B bank builder (#26). It is an
append-only JSONL file of `SlotRecord`s (`<run>/logs/slots.jsonl`) and writes one
canonical line per consumed slot.

### Reserving and consuming a slot

1. `reserve(cap_key, slot_id, *, study, method) -> SlotTicket` runs before any work.
   - The slot ID must belong to the cap key, and the method must fit the study. The cap
     key `A|<book>|<atom>` must never mix methods. A violation raises `LedgerError` and
     is not logged.
   - The cap is checked first. When `cap` slots are used (open or consumed), the request
     raises `SlotCapExceeded` and logs a `slot_cap` refusal.
   - Otherwise, an open slot ID raises `SlotReused` and logs `slot_reused`. A consumed
     one raises `SlotReused` and logs `slot_closed`, because a slot never reopens.
   - The ticket carries `slot_index`: `ids.slot_index(round, slot)` in Study A, or the
     cell slot in Study B. It also carries the open time.
2. `consume(record)` closes the ticket.
   - These fields must match the ticket: `slot_id`, `cap_key`, `slot_index`, `study`,
     `method`, `run_id` and `t_open_ms`.
   - The record's book (or bank, attempt and profile), atom, round and slot must match
     what the slot ID says, and `t_ms >= t_open_ms`.
   - A mismatch raises `SlotNotReserved`, and the ticket stays open. A second record
     for a consumed slot also logs a `slot_closed` refusal.
   - The record is validated against `slot-record.schema.json` before it is appended.
     If it fails, the slot is not charged and the ticket stays open.
3. `check_attempt(bank_id, attempt)` is for #26, before each Study B attempt. Attempt 5
   or later raises `AttemptCapExceeded` and logs an `attempt_cap` refusal (cap key
   `B|<bank>`, `requested` = `attempt-5`).
4. Read-only views: `used(cap_key)` (open plus consumed), `remaining(cap_key)`,
   `records(cap_key=None)` and `open_tickets()`.

### Refusals, reopening and threads

- **Refusals** go to `refusals`, or to `slot-refusals.jsonl` next to the ledger when
  none is given. A refusal never charges a slot.
- **Reopening.** The constructor cuts torn last lines of the ledger and the refusal log
  (`jsonio.repair_torn_tail`). Each cut is logged as a `log_repaired` timing event,
  through `timing` or `timing.jsonl` next to the ledger, and recorded in
  `ledger.repairs`. The constructor then reads the existing records. They must belong to
  `run_id`, have unique slot IDs, keep one method per cap key and stay within the cap,
  else `LedgerError`. They count toward the caps, so a resumed batch continues where it
  stopped.
- **Tickets** live in memory. A slot that was reserved but not consumed before a crash
  has no record and can be reserved again.
- **Threads.** `reserve`, `consume` and `check_attempt` are atomic per ledger, so the
  three methods' proposal windows can run in parallel. The tests race six threads over
  the same 12 slots and check that the ledger charges exactly 12.

### Call order for each proposer

| Proposer | Order |
| --- | --- |
| A1 (#19) | `reserve` when the designer opens a slot. `consume` on submit, or with `timeout` at the 40-s cap or at the end of the window. |
| A2 (#18) | `reserve`, sample or mutate, validate, `consume` (with `A2Detail`). |
| A3 | build the prompt, `reserve`, count, call, parse, validate, `consume` (section 7). |
| B (#26) | `check_attempt` per attempt; per slot, `BSlotProposer.propose_slot(cell, seed_namespace=)`, or the same steps by hand. |

## 7. A3 and B slots

`A3Proposer(client, ledger, prompt_set, decoding_schema, *, clock, reserved=None)` is
the `RoundProposer` for A3. `propose_round(request)` fills slots 1-3 in order and returns
their records.

Before anything is charged, the proposer checks the request: the method must be A3, the
semantic label present, the run ID equal to the ledger's, and the book and profile must
match `request.book`. A bad request raises `ValueError`.

Each slot then runs these steps:

1. Build the prompt. The prompt includes the request's same-round records and this
   round's earlier slots.
2. `reserve`.
3. If `request.window_end_ms` has already passed, close the slot as `timeout` with no
   call.
4. Count the tokens; this can give `overflow_input` or a failed count.
5. Make one `propose` call with `a3_seed_key(batch_ns, atom, round, slot)` and
   `slot_id=`. The status maps through `outcome_from_llm_status`.
6. Parse the output.
7. `validate` against `request.book.references()` with the book's threshold, then map
   the result with `outcome_from_validation`.
8. `consume`.

Two failures still close the slot as `invalid_json` with `llm_status="server_error"`,
with a logged warning: a client that raises, and a client that returns an unknown
status.

`BSlotProposer(client, ledger, prompt_set, decoding_schema, *, clock, threshold=None,
reserved=None)` runs one bank slot per `propose_slot(cell, *, seed_namespace)` call. It
follows the same steps, with these differences:

- the seed key is `b_seed_key(bank_ns, attempt, profile, atom, slot)`;
- validation runs against `cell.other_atom_references()`;
- a waveform equal to a retained option of the same cell is `duplicate`
  (`mode="B"`, `cell_duplicate`);
- `E_DUPLICATE` and `E_SEPARATION` against other atoms mean `incompatible`.

The bank builder (#26) keeps the first four `valid` options and owns the attempt logic.

Every slot record carries these fields:

- `seed_key` and `seed`;
- `prompt_sha256`, and `schema_sha256` (`jsonio.schema_sha256(decoding_schema)`);
- `llm_status`;
- `tokens_in` (the server's count, else the `/tokenize` count) and `tokens_out`;
- `latency_ms`;
- `raw_output`, clipped to the schema limit of 65,536 characters;
- the recipe and its hash, and the validator codes and messages;
- `pcm_sha256` and `file_sha256` (canonical WAV) whenever the waveform is usable.

## 8. DEMO fixtures and the example ledger

`av_generation._demo_ledger` runs one fixture per outcome code through the real A3 and
B code. It uses the scripted client, the committed prompt set, the DEMO meanings and
synthetic recipes. `E_NONFINITE` and `E_CLIP` cannot occur for in-domain recipes, so
two marker recipes have their validator result replaced.

`write_demo_ledger(dir)` writes the example run `DEMO-slot-ledger-01`, committed as
`generation/examples/demo-slot-ledger/`:

- `slots.jsonl` holds 19 records covering all 14 outcome codes, with 18 model calls and
  at most 1 per slot;
- `slot-refusals.jsonl` holds a 13th-slot refusal and a 5th-attempt refusal.

The test byte-compares a regenerated copy with the committed one. CI also uploads it, and
the ledger of the mock-server integration test, under `generation/out/ci/`.

## 9. Decisions

| Decision | Rationale |
| --- | --- |
| The Study B instruction reuses the A3 instruction byte for byte; only the context block differs (proposed in #17) | Study B §4 uses the same core proposer. Both hashes are frozen at G4. |
| The fixed instruction is committed | It is one protocol-fixed sentence that is already public in issue #17, and the acceptance test needs it byte for byte. It contains no vocabulary, meaning text, codebook or allocation. The meaning texts stay restricted (DEMO set in git). |
| The instruction is the `system` message and the context the `user` message | The instruction stays exactly as stored, and the pinned template's default system message is not added. |
| Canonical compact JSON context; features and scores rounded half-even to 4 decimals; ratings sorted | Deterministic and platform-independent, compact enough to keep the worst case at 30% (A3) and 62% (B) of the input limit, and the seat order never shows. |
| The context shows the published recipe schema and a validator-code glossary | The instruction says "only the supplied schema and allowed values". The schema matches the decoding schema's constraints (#16 owns the decoding schema and its hash). |
| Validator codes, not messages, in the feedback | `CandidateFeedback` carries codes only. Same-round entries follow the same rule, and the worst case stays bounded. |
| The builders filter inputs (book, atom, closed rounds, cell) instead of trusting the caller | The prompt is the last masking boundary. Leak tests run against hostile inputs. |
| The prompt is built before `reserve` | A contract violation raises before a slot is charged. Building a prompt is not proposal work. |
| A parser refusal is logged as validator code `E_JSON` with the parser message | One outcome mapping for every parsed slot. A3 sees why a slot failed. |
| A client exception or unknown status is `server_error` / `invalid_json` | A failed slot always closes and the next one proceeds (no retry). The warning keeps the cause visible. |
| A slot whose window has ended closes as `timeout` without a call | A proposer never runs past its proposal window (`RoundRequest.window_end_ms`). |
| The ledger checks the cap before reuse, and checks slot IDs against cap keys and methods | Any request beyond 12 is a `slot_cap` refusal. A slot can never be charged to the wrong book, cell or method. |
| Refusals are always logged (default file next to the ledger) | The acceptance criterion requires a 13th-slot request to be logged, even when the caller passes no writer. |
| `BSlotProposer` lives with A3 | Study B uses the frozen A3 core. #26 gets one tested slot path instead of a copy. |
| Offline token counts use the hand-written Qwen2.5 chat format, checked against the real template | Used only for notes. The run-time count comes from the server (`/tokenize`). |
