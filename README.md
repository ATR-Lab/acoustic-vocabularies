# Acoustic vocabularies

Public engineering monorepo for an experiment application and sound tooling.
Phase 1 contains architecture spikes only. Phase 2 requires an explicit G1 sign-off.

## Components

| Directory | Component | Language / format |
| --- | --- | --- |
| `sound/` | Sound rendering engine | Python |
| `generation/` | Generation tooling | Python |
| `banks/` | Bank schemas and synthetic fixtures | JSON / Python |
| `schedules/` | Scheduling tooling and synthetic fixtures | Python / JSON |
| `analysis/` | Analysis tooling | Python |
| `isaac/` | Simulator integration | Python |
| `unity/` | Headset application | C# / Unity |
| `ops/console/` | Operator console | To be decided at its implementation gate |
| `apparatus/` | Public apparatus metadata and empty result templates | JSON / CSV |
| `docs/adr/` | Architecture decisions | Markdown / JSON Schema |
| `docs/protocol/` | Protocol references only | Markdown |
| `tests/` | Automated engineering tests | Python |

## Checkout

Install Git LFS before cloning, then run `git lfs install` and `git lfs pull`.
On Windows, run `git config core.longpaths true` in this repository.
Text is normalized to LF; Windows command files use CRLF. Unity assets use visible
meta files and force-text serialization. Configure Unity Smart Merge with the
repository setup script when the Unity spike selects an editor.

The initial skeleton is the sole direct commit to `main`. Subsequent changes use
one branch and one pull request per issue; maintainers review and merge.
See [versioning](docs/versioning.md), [data policy](docs/data-policy.md) and
[third-party provenance](THIRD_PARTY.md). CI runs Python tests, schema validation,
credential/path checks and LFS-pointer checks on each pull request.

For local checks, use Python 3.12, create an isolated virtual environment, then
`python -m pip install -r requirements-dev.txt`, `python tools/generate-fixtures.py`, `python -m pytest`,
`python tools/validate_schemas.py`, and `python tools/repo_guard.py`.
The guard inspects committed history; run it again after committing.

Configure Smart Merge with `tools/setup-unity-merge.ps1 -UnityEditor <Unity.exe>`
on Windows, or `UNITY_EDITOR=<Unity.app/Contents/MacOS/Unity> sh
tools/setup-unity-merge.sh` on macOS. Each checkout configures its local editor;
editor paths are never committed.

Never commit study vocabularies, codebooks, learner packages, candidate banks,
allocation lists, confirmatory seeds, participant data, credentials or local
station identifiers. Keep methodology documents outside this repository.
Third-party assets require verified licenses before being committed.
