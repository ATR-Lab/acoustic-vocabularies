# Test fixtures (DEMO data only)

`package-demo/manifest.json` and `package-demo/audio.json` are byte copies of the sound
stack's committed DEMO example `sound/examples/package-demo/` (Study A, `demo: true`,
branch `o4.1.7-package`, #13). `tests/analysis/test_reconcile_rules.py` loads them through
`references._load_package`, so a change of the package JSON format fails a test here.
Refresh them with `git show <sound branch>:sound/examples/package-demo/<file>` when the
sound stack changes its example; never place study packages here.
