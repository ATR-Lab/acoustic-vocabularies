# G1 open risks and revisit triggers

| Risk | Observed condition / impact | Next evidence or action | Revisit trigger |
| --- | --- | --- | --- |
| Missing protocol inputs | Methodology/templates not located; exact log fields and model decoding unverified | Read external sources and reconcile every conflict | Source/version supplied or changed |
| LFS service unavailable | GitHub rejects synthetic binary upload; asset clone proof blocked | Restore server LFS and repeat hash round trip | LFS succeeds/fails or asset set changes |
| Nonbaseline Isaac host | Ubuntu 24.04, older Quadro RTX 6000 | Record feasibility data separately; qualify 22.04/required GPU or obtain explicit exception | Driver/OS/GPU/density change |
| Asset license provenance | Dataset card declares Apache-2.0; archive lacks separate notices | Review proposed pinned-dataset use; retain fetch-only policy; use URDF fallback if contrary evidence appears | New asset revision/notice |
| Headset absent | No deployed Quest measurements | USB/Link runs and headset captures | Runtime/device/refresh change |
| Bridge timing unknown | No full candidate matrix | Run 30-minute conditions and controlled interruptions | >250 ms steady gap or three-day overrun |
| Audio sync unqualified | Software timestamps do not establish ear-level onset | Independent physical synchronization and acoustic/electrical capture | p95 uncertainty >20 ms or route change |
| Input/legibility unmeasured | Compilation does not establish seated usability | Three internal testers, all commands, ladder and loss checks | p95 >3.5 s, accidental activation, unreadable labels |
| Capacity qualification incomplete | Short concurrent/multi-env probes passed; rendered run logged discarded frames | Sustained rendering/bridge concurrency and failure-isolation checks on qualified hardware | OOM, missed intervals, cross-station interference |
| Runtime pin provisional | Installed Unity is supported non-LTS; LLM untested | Review exact editor lifecycle; later pinned LLM validation | Dependency/runtime/model revision change |
| Public-data leakage | Generated study material must remain inaccessible | Review all exports/fixtures and keep raw evidence private | Any leaked cue, identifier, token or private path |

All risks remain open until linked evidence resolves them. Issue acceptance
criteria are unchanged. Proposed thresholds derive from issues pending protocol
reconciliation; do not turn their absence into a passing value.
