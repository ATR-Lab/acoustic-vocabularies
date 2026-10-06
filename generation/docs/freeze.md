# G4 generation freeze

Format `av-generation/freeze-manifest` version 1, freeze version **1.0**. Producer: #25
(`av_generation.freeze`, schema
[`../schema/freeze-manifest.schema.json`](../schema/freeze-manifest.schema.json)).
Consumers: the CI freeze guard, confirmatory batches (O7.1.1) and confirmatory banks
(#28) through `genconfig.check_run_config`, the apparatus manifest (five fields), and
OSF preregistration part 1 (O6.2.4: the frozen values).

Status: **no frozen manifest exists yet.** The repository carries a draft,
[`../FREEZE-v1.0.draft.json`](../FREEZE-v1.0.draft.json) (`status: draft`). Its code and
repository values are real and guarded. Its GPU-host, restricted-storage and human values
are pending. G4 needs the pilot generation panels (O6.2.1), the separation-threshold
check (O6.2.2), the GPU host and the owner and advisor sign-off (section 6).

## 1. What G4 freezes

| Issue #25 checklist item | Items | Filled by |
| --- | --- | --- |
| Renderer v1.0, renderer hash, implementation manifest (envelope, normalization target, limiter policy, sample rounding) | `renderer.version`, `renderer.hash`, `renderer.recipe_schema_hash`, `renderer.implementation`, `renderer.spec_sha256`, `renderer.golden_manifest_sha256` | code and committed files |
| Separation threshold from O6.2.2, for all methods and B banks | `separation.threshold`, `separation.evidence_sha256` | `sound/config/validator.json`; O6.2.2 summary hash (human) |
| Model revision, tokenizer revision, weights checksums, vLLM version, chat template hash, bf16, GPU | `model.*`, `runtime.*`, `llm.manifest_sha256` | constants; LLM manifest (#16); GPU host |
| A3 and B prompt hashes, schema hash | `prompts.a3_sha256`, `prompts.b_sha256`, `schema.decoding_sha256`, `meanings.sha256` | frozen generation config |
| Decoding (0.7, 0.9, 50, 1.0, 512, 16,384) | `decoding.*` | constants; `decoding.implementation` from the GPU host |
| Seed function and namespaces | `seeds.function`, `seeds.namespaces`, `seeds.reference_digest` | code |
| Budgets (A: 4 x 3 slots, 40 s, 20-s rating; B: 12 per cell, 576 per attempt, 4 attempts) | `budget.study_a`, `budget.study_b` | constants |
| A2 rules (steps, reflection, no restarts) | `a2.rules`, `generation.code` | constants and code |
| Selector, first-atom, tie and fallback rules | `selector.rules`, `generation.code` | constants and code |
| Fallback banks (64 per profile) and books | `fallback.bank_hash`, `fallback.banks_sha256`, `fallback.books_sha256` | restricted fallback build, re-rendered at the build |
| Pilot panel timing review | `pilot.timing_review`, `pilot.audit_sha256` | human (O6.2.1, #24 audit tables) |
| One config hash for every confirmatory run | `config.frozen_sha256`, `config.document` | frozen `generation-config.json` |
| Tag and sign-off | `repo_commit`, `tag`, `signoff` | human |

## 2. Manifest format

The manifest is one JSON document (indent 2, sorted keys, UTF-8, `\n`), readable without
the code:

| Field | Meaning |
| --- | --- |
| `format`, `format_version` | `av-generation/freeze-manifest`, `1` |
| `freeze_version` | `1.0` for G4; a later freeze is `1.1`, `2.0`, ... (`FREEZE-v1.1.json`) |
| `status` | `draft` or `frozen` |
| `description` | one paragraph; a draft starts with `DRAFT - NOT FROZEN` |
| `protocol_version` | protocol version the values belong to |
| `repo_commit` | the commit the values were collected from (`null` in a draft) |
| `tag` | the signed tag of the commit that adds the manifest, proposed `generation-freeze-v1.0` (`null` in a draft) |
| `items` | one object per key of `freeze.REQUIRED_ITEM_KEYS`, in that order |
| `apparatus` | the apparatus-manifest fields, derived from the items |
| `signoff` | `{role: owner or advisor, date, reference: link to the sign-off comment}`; never names |

Each item has `key`, `category` (the first part of the key), `value`, `sha256`, `source`,
`guard` and `path`:

- `value` is any JSON value. `null` means **pending**: only a draft may have pending items.
- `sha256` is the value itself for hash items, the canonical SHA-256
  (`jsonio.canonical_sha256`) of the value for objects and arrays, and `null` for other
  values and for pending items.
- `source` says where the value comes from and why it is frozen. A pending item's
  source starts with `PENDING` and gives the command that fills it.
- `guard` says how the CI freeze guard checks the item (section 4):

| Guard | Checked by |
| --- | --- |
| `code` | recomputed from the running code at every guard run |
| `file` | recomputed from the committed file at `path` at every guard run |
| `config` | copied from `config.document`; the guard checks the document's hash, compares it with the other items and with the running code (`genconfig.config_differences`) |
| `recorded` | recorded at the freeze from the GPU host, restricted storage or a human decision; the guard checks its form and that it is present in a frozen manifest |

A frozen manifest has no pending item, a `repo_commit`, a `tag`, an owner and an advisor
sign-off, and a non-DEMO config. `manifest_problems(manifest)` lists every violation of
these rules and of the schema.

Apparatus fields: `renderer_recipe_schema_hash` = `renderer.recipe_schema_hash`;
`model_revision` = `model.revision`; `runtime_precision` = `runtime.precision`;
`fallback_bank_hash` = `fallback.bank_hash`; `prompt_hash` =
`canonical_sha256({"a3_sha256": prompts.a3_sha256, "b_sha256": prompts.b_sha256})`,
which is the canonical hash of the frozen config's `prompts` object.

## 3. Items

| Key | Guard | Value |
| --- | --- | --- |
| `config.frozen_sha256` | config | `GenerationConfig.frozen_sha256()` of `config.document`: the config hash of every confirmatory run |
| `config.document` | config | the frozen `generation-config.json` (architecture section 11) |
| `renderer.version` | code | `av_sound.renderer.RENDERER_VERSION` (1.0.0 from G4) |
| `renderer.hash` | code | `av_sound.version.renderer_hash()` |
| `renderer.recipe_schema_hash` | code | `av_sound.version.renderer_recipe_schema_hash()` |
| `renderer.implementation` | code | `av_sound.version.renderer_manifest()`: envelope lengths, RMS target, full scale, minimum event, table and code digests (its canonical hash is `renderer.hash`) |
| `renderer.spec_sha256` | file | SHA-256 of `sound/docs/renderer-spec.md`, the implementation manifest (D1 rounding, D2 envelope, D6 normalization, D7 no limiter) |
| `renderer.golden_manifest_sha256` | file | SHA-256 of `tests/golden/manifest.json` |
| `validator.version`, `validator.hash` | code | `VALIDATOR_VERSION`, `av_sound.store.validator_code_hash()` |
| `validator.reserved_sha256` | file | reserved-signal digest of `sound/reserved/registry.json` |
| `separation.threshold` | file | `separation_threshold` of `sound/config/validator.json` (decimal text) |
| `separation.evidence_sha256` | recorded | SHA-256 of the O6.2.2 summary from the listening tool (#23) |
| `model.id`, `model.revision` | code | `constants.MODEL_ID`, `constants.MODEL_REVISION` |
| `model.tokenizer_revision` | recorded | tokenizer revision SHA (LLM manifest) |
| `model.weights_sha256` | recorded | `jsonio.file_set_sha256({file name: SHA-256})` over the `*.safetensors` files (`freeze.weights_sha256(model_dir)`) |
| `model.license` | recorded | model licence |
| `runtime.vllm_version`, `runtime.cuda_version`, `runtime.driver_version`, `runtime.gpu` | recorded | GPU host |
| `runtime.precision`, `runtime.max_model_len`, `runtime.chat_template_sha256` | recorded | LLM manifest (#16) and server log (planned: bfloat16, at least 16,896) |
| `decoding.temperature` .. `decoding.max_input_tokens` | code | `constants.FROZEN_DECODING`, `constants.MAX_INPUT_TOKENS` |
| `decoding.implementation` | recorded | structured-output path of the frozen runtime (#16) |
| `schema.decoding_sha256`, `prompts.a3_sha256`, `prompts.b_sha256`, `meanings.sha256`, `llm.manifest_sha256` | config | the shared hash definitions of `jsonio`, `prompts` and `meanings` |
| `seeds.function` | code | `genconfig.SEED_FUNCTION` |
| `seeds.namespaces` | code | key namespaces, key formats, batch and bank namespace rules, bank ID patterns |
| `seeds.reference_digest` | code | `seeds.seeds_digest(freeze.reference_seed_keys())` over 2,880 keys: any change to the derivation changes it |
| `budget.study_a`, `budget.study_b` | code | every budget constant and its derived totals |
| `a2.rules`, `selector.rules` | code | step sets and thresholds from `constants`, and the rule texts `freeze.A2_RULES_TEXT`, `freeze.SELECTOR_RULES_TEXT` |
| `generation.code` | code | `{module: code digest}` of `freeze.GENERATION_CODE_MODULES` (proposers, prompts, parser, ledger, LLM client, selector, orchestrator); Python 3.11 AST without docstrings, as for the renderer hash. Pending in the draft (section 8) |
| `fallback.bank_hash`, `fallback.books_sha256` | config | `fallback_bank_hash` and `book_sha256` per profile of the restricted fallback build |
| `fallback.banks_sha256` | recorded | `bank_sha256` per profile, filled by `build` after re-rendering |
| `pilot.timing_review` | recorded | `{panel_sessions, pilot_books, max_atom_minutes, within_budget or bookings_extended, reference}` |
| `pilot.audit_sha256` | recorded | `{set: SHA-256}` of the #24 audit tables of the pilot books |

## 4. CI freeze guard

`tests/generation/test_freeze_manifest.py::test_ci_freeze_guard` runs in the generation
workflow on Linux, macOS and Windows:

```python
freeze_differences(active_manifest_path(), current_values()) == []
```

- `active_manifest_path()` is the highest frozen `generation/FREEZE-vX.Y.json`, or the
  draft when none exists.
- `current_values()` recomputes every `code` and `file` item from the running code and the
  committed files.
- `freeze_differences` reports every `manifest_problems` finding, every item whose current
  value differs (pending items of a draft are skipped), and every field of
  `config.document` that differs from the running code.

Before G4 the guard checks the draft. A change to a guarded value, for example a new
threshold after O6.2.2 or the renderer bump to 1.0.0, fails the test until the draft is
refreshed in the same pull request:

```bash
uv run --project generation python -m av_generation.freeze refresh
```

After G4 the guard checks `FREEZE-v1.0.json`. Any change to a frozen value fails CI. The
fix is not a refresh: it needs a new protocol version and a new freeze
(`FREEZE-v1.1.json`), with the reason recorded (Study A protocol section 3.6). The same
check runs by hand:

```text
$ python -m av_generation.freeze check DEMO-FREEZE-changed.json   # temperature 0.7 -> 0.8
FREEZE GUARD FAILED: DEMO-FREEZE-changed.json (draft) differs from the repository:
  - decoding.temperature: 0.8, but config.document has 0.7
  - decoding.temperature: frozen 0.8, current 0.7
```

The tests also show the guard passing on unchanged draft and frozen manifests, and
failing for changed code values, committed files, the config document, the apparatus
fields, missing or duplicated items and missing sign-off.

## 5. Fallback re-render check

```bash
uv run --project generation python -m av_generation.freeze verify <manifest> --fallback <fallback-manifest.json>
```

`verify_fallback_hashes(manifest, fallback)` does these steps:

1. It runs `av_sound.fallback.verify_fallback`. Every bank and book recipe must render
   to its recorded waveform and be admissible in acceptance order.
2. It renders every fallback-book atom again and recomputes each book digest from the new
   samples.
3. It compares `fallback_bank_hash`, the book and bank digests, the renderer and
   validator versions, the reserved-signal digest and the threshold with the manifest.

`build` runs the same check and refuses to write a manifest whose fallback set does not
reproduce. With the public DEMO set (`sound/testvectors/fallback/demo-manifest.json`):

```text
fallback reproduces DEMO-FREEZE-v1.0.json: renderer 0.1.0, fallback_bank_hash 549f0c47e5435dab92bd6a2bec2cf41563c270545cded9ab4355e25887ff7571
  book P1 0b38a2bfb78a8d47ccd6de55ea8c5661819567d77049216e77527f210883fdad (re-rendered, 16 atoms)
  book P2 bcaa4a6ca29a6a4d973276b7ffb9ae6825b191886826713113db66b640db54e4 (re-rendered, 16 atoms)
  book P3 bfb26ea3347cfab29fea1c3aae5a01492815229abdeadea71095efbb163c24d2 (re-rendered, 16 atoms)
```

## 6. G4 procedure

Prerequisites: O6.2.1 and O6.2.2 are done, the threshold is decided, the renderer is
bumped to 1.0.0 (sound stack: renderer version, golden manifest, DEMO fallback manifest),
and the restricted fallback set is rebuilt at the frozen threshold
(`sound/docs/fallback.md` section 8). Run every command from the repository root, on
a work copy of the commit to freeze. Keep restricted files outside the repository.

1. **Copy the draft:** `cp generation/FREEZE-v1.0.draft.json <restricted>/freeze-draft.json`.
2. **GPU host (human/hardware):** record each runtime value, for example:

   ```bash
   python -m av_generation.freeze record runtime.vllm_version --text 0.x.y \
       --source "GPU host report <date>: vllm.__version__" --manifest <restricted>/freeze-draft.json
   python -m av_generation.freeze weights <model snapshot dir>   # -> model.weights_sha256
   ```

   Record `runtime.*`, `decoding.implementation` and the `model.*` values (confirm the
   development-time values in the draft).
3. **Evidence (human):** record `separation.evidence_sha256` (O6.2.2 summary),
   `pilot.timing_review` and `pilot.audit_sha256` (#24 audit tables of the pilot books).
4. **Frozen config:** build the frozen `generation-config.json` with
   `genconfig.build_generation_config` (the same call as the pilot runs), using the
   frozen LLM manifest, decoding schema, prompt sets, meaning set, threshold and
   restricted fallback set.
5. **Sign-off (human):** the owner and the advisor comment on #25 with the draft's
   `table` output. Copy the links of the comments.
6. **Build:**

   ```bash
   python -m av_generation.freeze build --status frozen \
       --draft <restricted>/freeze-draft.json --config <restricted>/generation-config.json \
       --fallback <restricted>/fallback-<date>/fallback-manifest.json \
       --protocol-version <v> --repo-commit <sha> --tag generation-freeze-v1.0 \
       --signoff owner <date> <comment link> --signoff advisor <date> <comment link> \
       --out generation/FREEZE-v1.0.json
   ```

   `build` takes the recorded values from the draft and the config values from the
   config, and recomputes the code and file values. It re-renders the fallback set and
   refuses a pending item, a DEMO config or fallback set, a missing sign-off, or an LLM
   manifest in the repository that does not hash to the config's value.
7. **Check:** run `python -m av_generation.freeze check` (it now picks
   `FREEZE-v1.0.json`) and `verify generation/FREEZE-v1.0.json --fallback <restricted manifest>`.
8. **Commit and tag (human):** delete the draft, commit `FREEZE-v1.0.json`, create a signed
   tag (`git tag -s generation-freeze-v1.0`) and push. Copy the five `apparatus` values
   into the apparatus manifest. Record the tag and the manifest's file SHA-256 on #25.
9. **Runs:** confirmatory batches and banks use
   `python -m av_generation.freeze config generation/FREEZE-v1.0.json --out <run>/generation-config.json`
   (section 7).

## 7. Confirmatory runs

```python
loaded = freeze.load_freeze_manifest("generation/FREEZE-v1.0.json", require_frozen=True)
config = freeze.frozen_config(loaded.manifest)  # or the run's generation-config.json
genconfig.check_run_config(config, kind="confirmatory", freeze_manifest=loaded.manifest)
# RunManifest.freeze_manifest_sha256 = loaded.sha256
```

`check_run_config` refuses a draft (`E_FREEZE_STATUS`) and a config whose hash differs
from `config.frozen_sha256` (`E_FREEZE_MISMATCH`). Pilot runs record their own unfrozen
config hash. Pilot books and banks keep their pilot IDs (`A-P..`, `bank-P..`) and stay
archived. `bank_manifest.require_bank_set` refuses a pilot bank in a confirmatory run.

## 8. The committed draft

`generation/FREEZE-v1.0.draft.json` is regenerated from the repository and checked by the
guard. It contains:

- every `code` and `file` item, from this commit. `generation.code` stays pending while
  #16-#20 implement those modules: the code digests are taken by `build` at G4.
- three development-time values from Hugging Face API metadata at the pinned revision:
  `model.weights_sha256` (from the LFS SHA-256 of the four shards), `model.tokenizer_revision`
  and `model.license`. Confirm them on the GPU host at G4.
- every other item pending, with the command that fills it.

Keep it current with `python -m av_generation.freeze refresh`. Delete it in the G4 commit.

## 9. Decisions

| Decision | Rationale |
| --- | --- |
| The committed file is a draft (`FREEZE-v1.0.draft.json`, `status: draft`); `FREEZE-v1.0.json` appears only at G4 | the gate needs pilot evidence, the GPU host and sign-off; a draft can never start a confirmatory run (`E_FREEZE_STATUS`) |
| Items carry `guard` and `path` (schema change) | a reader sees how each value is protected; the guard needs no knowledge outside the manifest |
| The frozen config document is an item (`config.document`) | one self-contained file; the config hash, the config items and the running code are cross-checked |
| Extra items: `config.document`, `renderer.implementation`, `renderer.spec_sha256`, `validator.reserved_sha256`, `separation.evidence_sha256`, `seeds.reference_digest`, `fallback.banks_sha256`, `pilot.audit_sha256`, `generation.code` | the checklist names the implementation manifest, the O6.2.2 evidence, both fallback parts, the pilot audits and frozen rules; values alone do not freeze code |
| `generation.code` is pending in the draft and computed by `build` | the modules are implemented by parallel issues before G4; a digest in the draft would go stale with each of them |
| `prompt_hash` = canonical hash of the config's `prompts` object | the apparatus has one prompt field for two prompt sets |
| `model.weights_sha256` = `file_set_sha256` of the `*.safetensors` files | the shared file-set definition; computable from Hugging Face LFS metadata and on the GPU host |
| Threshold compared as text | the frozen value is the exact decimal text of `sound/config/validator.json` |
| Proposed tag `generation-freeze-v1.0` (signed) | names the gate and the freeze version; the manifest records it |
| Sign-off records roles, dates and links only | public repository: no names |
