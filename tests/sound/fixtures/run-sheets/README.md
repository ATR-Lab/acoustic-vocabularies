# Run-sheet package-hash schema (fixture)

Byte copy of `schedules/schema/package-hashes.schema.json` from the run-sheets stack
(#32, branch `o4.4.4-run-sheets`). `tests/sound/test_package.py` validates the mappings
that `av_sound.package.package_hashes` emits against it. Refresh it when that schema
changes; once the stacks merge, the tests can read the published schema instead.
