# Never-started menu cancellation

Refs #158, #82, #81, #70.

An actual build008 Study B active-menu attempt failed readiness before the menu
started. Its ledger contained a header and `menu_interrupted` at 29338.0902 ms,
before the proposed start at 29840.8162 ms. Explicit resume created a fresh
module lease but reused the intentionally failed visit ledger, causing
`MENU_LEDGER_UNAVAILABLE`. The retained private ledger SHA-256 is
`c0deb2e48e4009beb1b77956578b7a4f303190132426286d7d1a3b6cbd407f8f`;
the corresponding raw data journal SHA-256 is
`4cb80a8fd52ee15a87987e7569f8edad477836747910a76aceccbdf53b25231c`.
The failed run is preserved.

Runtime correction `12e7292ebb27ff627b995eaf06ab3c6a1c190556` makes a
never-started timeline terminal and hidden without inventing a menu interruption
record. Engine, data and frame cancellation records remain. Only a fresh owned
lease may subsequently use the untouched visit ledger. Once `Start` is attempted,
interruption still permanently invalidates the ledger, even before its first
visible display or play. The change never clears an existing ledger failure,
changes a slot deadline, or undoes consumed/uncertain engine intent.

Unity compiled the regression suite. The initial run passed 25 of 28 cases;
three new cases failed because the test reader's Windows file-sharing mode
conflicted with the still-open writer. Test-only correction
`76fa8bb0ec8b47620b7650d411f79daf4305a897` uses compatible read-only access.
All three reran successfully, with no skips, at integration source `3b6a49d`.
The corrected test XML SHA-256 is
`b3eab40c0bd8998d8ad326c0b69522771f0f94dfdbeff5cfd69ba381ff7cfd8b`.
This establishes 28 unique passing cases across the retained runs, not one
uninterrupted 28-case pass. The engine/multiplexer/timeline/ledger regression
covers both profile and atom menus, fresh request IDs and the normal 750 ms
lead. Started-menu failure remains unsealable.

Schema validation passed (45 schemas, 14 examples), as did whitespace checks,
the public-history guard and required repository CI at `76fa8bb`. Native
recovery validation is still pending. These software tests do not qualify a
complete visit, acoustic delivery, physical display timing or participants.
