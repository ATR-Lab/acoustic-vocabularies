# Interface contracts

Contracts between components that are built separately. Each entry names the
producer, the consumers and the file that defines the contract. Entries marked
*pending* are added by the pull request for that issue.

| Contract | Producer | Consumers | Definition |
| --- | --- | --- | --- |
| Motif recipe (JSON) | #7 | renderer #8, validator #9, generation #16–#20, banks #26 | [`sound/schema/recipe.schema.json`](../../sound/schema/recipe.schema.json), [`sound/docs/renderer-spec.md`](../../sound/docs/renderer-spec.md) |
| Sound engine Python API (`render`, `validate`, `nearest_reference`, `compose_message`, store, fallback) | #8–#15 | generation #16–#25, banks #26–#28, analysis #33–#35 | [`sound-engine.md`](sound-engine.md) *(pending, #8)* |
| Message byte contract (action + 200 ms + referent) | #10 | Unity audio subsystem #64 | [`sound/docs/composition.md`](../../sound/docs/composition.md) *(pending, #10)* |
| Reserved-signal registry | #14 | validator #9, Unity #64, #68, #70 | [`sound/reserved/registry.json`](../../sound/reserved/registry.json) *(pending, #9 and #14)* |
| Package format (manifest, answers, held-out hashes) | #13 | Unity audio subsystem #64, session engine #67 | [`package-format.md`](package-format.md), `sound/schema/package.schema.json` *(pending, #13)* |
| Curriculum, permutation, schedules, allocation, run sheets | #29–#32 | package builder #13, session engine #67, operator console #73, round orchestrator #20 | [`schedules.md`](schedules.md), `schedules/schema/` *(pending, #29)* |

Rules for every contract:

- JSON Schemas use Draft 2020-12 and forbid extra properties.
- Every published format carries a version field. A breaking change bumps the major
  version and is announced in the consumer's issue.
- Hashes are lowercase hex SHA-256. Hash-bearing files use `\n` line endings and are
  never subject to line-ending conversion.
- Examples in this repository are synthetic and labelled as such. Study vocabularies,
  packages, banks, schedules with confirmatory seeds and allocation lists are kept in
  restricted storage; only their hashes may appear here.
