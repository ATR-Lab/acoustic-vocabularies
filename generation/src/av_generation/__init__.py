"""Study A generation system and the contracts shared with the Study B bank builder.

Import submodules directly (`from av_generation.seeds import derive_seed`); this package
does not re-export names. The module map, the owner issue of each module and the log
contracts are in `generation/docs/architecture.md`; the cross-team API summary is in
`docs/interfaces/generation.md`.

Shared contracts implemented in the skeleton (do not change without a contract update):
`constants`, `ids`, `seeds`, `outcomes`, `domain`, `jsonio`, `records`, `config`,
`proposers`, `meanings`, `genconfig`, `panel_session`, `rater_protocol`, `rundir`,
`masking`, `clock`, `netguard`, `webserve`, `llm_fake`, `bank_manifest` (manifest,
hash and amendment log). Interfaces filled by their owner issues: `llm` (#16), `ledger`,
`prompts`, `parser`, `a3` (#17), `a2` (#18), `a1` (#19), `orchestrator`, `selector`
(#20), `panel`, `rater` (#21), `dryrun` (#22), `threshold` (#23), `audit` (#24),
`freeze` (#25); #26 adds the bank builder in `banks/`.
"""

__version__ = "0.1.0"
