# G4 freeze assembly

`python -m av_generation.freeze_assembly` (#308, for #25) fills the restricted copy of the
G4 freeze draft in one checked step and prints the table the owner and the advisor sign off
on. The manifest format, the items, the CI freeze guard and the G4 procedure are in
[`freeze.md`](freeze.md); this page covers the assembler only. It replaces the hand-typed
`freeze record` calls of [`freeze.md`](freeze.md) section 6, steps 2 and 3. `freeze build`
(step 6) and its sign-off stay as they are.

```bash
# GPU host, after bringup.sh and evidence.sh (#16; generation/docs/llm.md section 7)
uv run --project generation python -m av_generation.freeze_assembly fill \
    --draft <restricted>/freeze-draft.json \
    --llm-manifest generation/llm/manifest.json \
    --evidence <restricted>/llm-evidence \
    --vllm-lock <vllm-env>/av-vllm-lock.txt \
    --inputs <restricted>/g4-inputs.json \
    [--config <restricted>/generation-config.json] [--out <restricted>/freeze-filled.json]

uv run --project generation python -m av_generation.freeze_assembly status \
    --draft <restricted>/freeze-draft.json            # the sign-off table (Markdown)
```

`--llm-manifest`, `--evidence` and `--vllm-lock` go together; `--inputs` can be given in
the same run or later, so the GPU host and the human items can be filled at different
times. Exit codes: 0 written, 2 refused (nothing is written; the error lists every
finding).

## 1. What `fill` does

1. It refreshes the `code` and `file` items of the draft (`freeze refresh`).
2. It takes the GPU-host items from the LLM manifest after `llm_manifest record-hardware`,
   with the values that `freeze build` compares (`llm_manifest.freeze_values`). The
   evidence directory must back them. The table lists the criterion of `acceptance.md`
   that must be PASS for each item:

   | Items | From the LLM manifest | Backing criterion (`acceptance.md`) |
   | --- | --- | --- |
   | `model.tokenizer_revision`, `model.weights_sha256`, `runtime.chat_template_sha256` | `model.*`, `chat_template.sha256` | start refusals: a changed revision and a changed weight shard are refused (both `.exit` files hold 2) |
   | `runtime.vllm_version`, `runtime.cuda_version`, `runtime.driver_version`, `runtime.gpu` | `runtime.version`, `hardware.*` | hardware record |
   | `decoding.implementation` | `llm_manifest.decoding_implementation` | 300 of 300 benchmark calls log the frozen decoding values |
   | `runtime.precision`, `runtime.max_model_len` | `runtime.precision`, `runtime.max_model_len` | worst-case p95 under the slot cap on the GPU |
   | `model.license` | `model.license` | none (Hugging Face metadata) |
   | `runtime.vllm_lock_sha256` | the vLLM lock (section 2) | none; the lock must pin the manifest's vLLM version |

   It refuses the evidence (`E_EVIDENCE`, `E_MOCK`, `E_MISMATCH`) when any of these is
   true:
   - a file it reads is missing: `acceptance.md`, `bench/llm_latency_summary.json`, or
     the `.exit` or `.log` file of either start refusal;
   - a backing criterion is not PASS, or `acceptance.md` has no row for it;
   - the benchmark was a MOCK run;
   - the benchmark's runtime, its hardware label (the label `evidence.sh` uses by
     default) or the hardware record in `acceptance.md` name another GPU, VRAM, driver,
     CUDA, vLLM or PyTorch version than the manifest;
   - a value differs from the committed `generation/llm/manifest.json`. `freeze build`
     refuses this case too. If the hardware record is not committed yet, `fill` prints a
     note.

   Other criteria (timeout cap, seeds, outbound connections, repeat test) back no freeze
   item. `fill` prints their status, and #16 tracks them.
3. It records `runtime.vllm_lock_sha256` from the vLLM lock (section 2).
4. It takes `separation.evidence_sha256`, `pilot.timing_review` and `pilot.audit_sha256`
   from the inputs file (section 3), each with its source.
5. Each value goes through `freeze.record_value`, so the checks of `freeze record` apply
   (draft status, value kinds, a source for every value). For each item `fill` prints
   `new`, `confirmed` (the draft already held the same value; for example, the
   development-time model values) or `changed from ...`.
6. The filled draft must pass the CI freeze guard (`freeze check`) before it is written.
   The file is written in one step (a temporary file, then a rename). It is never
   written inside a git work tree or this repository (`E_POLICY`), because it holds
   restricted values.

## 2. The vLLM environment lock (`runtime.vllm_lock_sha256`)

The lock pins the whole server environment, not only the vLLM version. The value is the
SHA-256 of the sorted lines of `pip freeze --all` in the vLLM environment. Blank lines and
`#av ` note lines are left out. Each line ends with `\n`, and the sort is by code point
(the order of `LC_ALL=C sort`):

```bash
grep -v '^#av ' <lock> | LC_ALL=C sort | sha256sum      # equals runtime.vllm_lock_sha256
```

`bringup.sh` writes the lock with
`python -m av_generation.freeze_assembly lock --python <vllm-env>/bin/python --out <file>`.
This command runs `pip freeze --all` and `import torch; print(torch.version.cuda)` in that
environment. It writes a note line `#av torch.version.cuda: <x>`, followed by the sorted
package lines. If the environment changed since the previous lock, it prints the old
digest. A hand-made `pip freeze --all > <file>` gives the same digest, but has no CUDA
note. The lock stays in restricted storage, because `pip freeze` can name local paths
(`pkg @ file:///...`). For the same reason, `lock` refuses to write inside a git work
tree. Only its hash goes into the manifest.

`fill` refuses a lock whose `vllm==` line differs from the manifest's `runtime.version`,
and a lock whose `torch==` version differs from `hardware.torch_version`. The local label,
such as `+cu128`, is ignored in that comparison.

## 3. The inputs file

One restricted JSON file, schema
[`../schema/g4-inputs.schema.json`](../schema/g4-inputs.schema.json). Items that are left
out stay pending. Each source is copied into the freeze manifest, so it must be public
text: roles and links only, with no names, host names, addresses or local paths.

```json
{
  "format": "av-generation/g4-inputs",
  "format_version": 1,
  "items": {
    "separation.evidence_sha256": {"value": "<sha256 of the threshold-check summary>",
                                   "source": "threshold check (#23): summary SHA-256; decision <link>"},
    "pilot.timing_review": {"value": {"panel_sessions": 6, "pilot_books": 3,
                                      "max_atom_minutes": 18.5, "within_budget": true,
                                      "reference": "https://github.com/<org>/<repo>/issues/25#issuecomment-<n>"},
                            "source": "pilot panels: timing review <link>"},
    "pilot.audit_sha256": {"value": {"<pilot set>": "<sha256 of its #24 audit tables>"},
                           "source": "pilot panels: audit tables (#24)"},
    "meanings.sha256": {"meaning_set": "<restricted meaning-set directory>",
                        "source": "frozen meaning set"}
  }
}
```

- `pilot.timing_review` holds counts, the longest atom in minutes and the link to the
  review. `within_budget` must agree with `max_atom_minutes` and the 20-minute atom
  budget. When it is `false`, `bookings_extended: true` is required.
- `meanings.sha256` is either the hash (`value`) or a meaning-set path (`meaning_set`,
  relative to the inputs file). This item is a `config` item: the draft cannot hold it
  before `config.document` exists, and `freeze build` copies it from the frozen config.
  So `fill` does not record it. With `--config`, `fill` refuses a frozen config that
  names another meaning set.
- Fallback items and the config itself stay with
  `freeze build --config ... --fallback ...`, as before.

## 4. `status`: the sign-off table

`status` prints one row per item of `freeze.REQUIRED_ITEM_KEYS`, in manifest order, with
these columns: key, state, value, guard, filled by and source. Hashes and revisions are
shortened to 12 characters, and objects are shown with their field count and canonical
SHA-256. The states are:

- `repository`: a `code` or `file` item, computed from this commit;
- `recorded`: a value recorded in the draft (or copied from the config in a built
  manifest);
- `pending`: no value yet. "Filled by" names the role only: GPU host, threshold check,
  pilot panels or owner at G4 (the config items, `generation.code` and the fallback
  items, which `freeze build` fills).

The header line gives the file SHA-256 and the counts. Post the table on #25 for the
owner and advisor sign-off. Their comment links go into
`freeze build --signoff owner|advisor <date> <link>` ([`freeze.md`](freeze.md) section 6).

## 5. Development-time values

The draft carries three model values from Hugging Face API metadata at the pinned revision
(`model.tokenizer_revision`, `model.weights_sha256`, `model.license`). They can be checked
again without downloading weights:

```bash
curl -s "https://huggingface.co/api/models/<model id>/revision/<revision>?blobs=true" > <tmp>/api.json
# the four *.safetensors LFS SHA-256 values -> jsonio.file_set_sha256 == model.weights_sha256;
# sha == revision == tokenizer_revision; tag license:<id> == model.license;
# every file's blob ID and size == generation/llm/manifest.json
```

At G4, `fill` confirms them against the GPU host's manifest; the output says `confirmed`
for each. The renderer values (`renderer.*`) are `code` and `file` items: `freeze refresh`
and `freeze check` confirm them on every commit, and the golden tests (`tests/golden`)
confirm that the renderer reproduces them. They change once more at G4: the renderer goes
to 1.0.0, together with the golden manifest and the DEMO fallback manifest
([`freeze.md`](freeze.md) section 4).

## 6. Decisions (proposed; the owner confirms them on #25 or #308)

| Decision | Rationale |
| --- | --- |
| The vLLM lock is a new recorded item, `runtime.vllm_lock_sha256` | one hash-pinned document; the CI guard checks its form, and the G4 handoff converter (#193) and the GPU-host scripts (#301) pass with it |
| Its pending source starts `PENDING (G4 assembly)`, not `PENDING (GPU host)` | `fill` fills it from the lock, and `llm_host freeze-commands` (#301) fills only the 8 manifest items |
| `runtime.cuda_version` is recorded from `hardware.cuda_version` (nvidia-smi), and its source also gives `torch.version.cuda` from the lock (D9 is still open) | `freeze build` compares the item with that manifest field; the source shows both values |
| `meanings.sha256` is checked and not recorded | it is a `config` item; `manifest_problems` refuses a config item without `config.document` |
| `pilot.timing_review` = `{panel_sessions, pilot_books, max_atom_minutes, within_budget, bookings_extended?, reference}`; counts are integers and `reference` is a link | the shape in [`freeze.md`](freeze.md) section 3, made checkable |
| The benchmark must carry the default hardware label of `evidence.sh` | the label ties the benchmark to the recorded hardware |
| `model.license` needs no acceptance criterion | it is Hugging Face metadata, and no GPU-host test can confirm it |
