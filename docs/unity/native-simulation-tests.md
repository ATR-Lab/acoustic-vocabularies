# Native simulated visits (#81)

This entry exercises the joined application with a deliberately compiled
`SIMULATION_TEST` capability. It is separate from the ordinary joined and
participant players. `ParticipantAdmission` remains false. No acoustic onset,
physical headset qualification, reviewer approval, or remote-clock calibration
is created by a simulated visit.

The current Windows build is an engineering test artifact. A successful build
does not establish that a native visit ran. The first attempted launch of build
`simulation-native-003` was blocked by Windows Application Control before a
process or player log existed. The installed policy must permit execution through
the normal operator approval path before native validation can proceed. The
implementation does not alter that policy or provide an alternate launch path.

## Build and provision

Use `tools/build-unity.ps1 -Target Windows -Scene SimulationTest` with a fresh
build ID, protocol `simulation-test-v1`, and the existing reviewed G1 description.
The builder adds `AV_SIMULATION_TEST` only to this build. Ordinary builds do not
compile the capability. The resulting `experiment.exe.build.json` binds every
player file and the source commit.

`tools/prepare_mock_visits.py` prepares private synthetic material attestations
from an actual sealed DEMO package and actual producer schedules. The separate
joined stager retains package, schedule, run-sheet, neutral, and configuration
hash checks. Every simulation review records its explicit scope, role, fixture
set hash, and exact material bindings; it cannot be passed to the ordinary review
loaders as an approval.

Keep each run in a fresh private `.local`, `private`, or `local-data` subtree
containing a `simulation-test-...` path segment. Independently pin the raw joined
configuration and capability bytes. The capability binds build ID, protocol,
package, schedule, output directory, and low simulation gain. Both package and
schedule must carry their producer DEMO marker. The ordinary joined constructor
still refuses the same fixture when acoustic calibration is absent.

Provision only the four files named in the stager's preparation report, preserving
and restoring any existing persistent files. Connect the independently pinned
actual Isaac command session and public source; neither the stager nor the player
launches the backend or invents health/reset replies.

After normal OS execution approval, launch the simulation player with:

```text
-joinedConfig <private join.local.json>
-joinedConfigSha256 <independent raw SHA256>
-simulationTestConfig <private simulation-test.local.json>
-simulationTestConfigSha256 <independent raw SHA256>
-simulationQuitOnComplete
-logFile <fresh private native.log>
```

The participant view carries `SIMULATION TEST — NO PARTICIPANTS`. The console
uses its separately pinned `--simulation-config` route and the normal durable
mailbox transport. The operator must explicitly start grammar and then explicitly
start the visit after grammar completion. Every later block still requires the
normal explicit boundary command. No slot duration or clock is compressed.

If the twelve scheduled A/B visits are exercised on one engineering day, retain
their actual calendar UTC values and previous-visit anchors. Use the normal
console's explicit `visit_window` or `pair_window` simulation deviation when an
interval is outside its permitted window. Missing anchors still refuse startup.
Never backdate an anchor or change a clock to pretend that days or weeks elapsed.
The delayed-visit fixture loader checks currently recorded here do not constitute
those later console sessions; their console configurations and interval deviations
must be prepared and retained before execution.

## Dummy responses and observations

First exercise representative panel controls manually in the Simulator. Keep the
player focused; focus loss retains the ordinary fault latch. The optional bulk
driver can then be armed by a create-new private
`<capability.output_directory>/arm-dummy-inputs.local.json` containing exactly:

```json
{"version":1,"scope":"SIMULATION_TEST","session_nonce":"<current native nonce>","capability_sha256":"<raw capability SHA256>"}
```

The driver persists its origin and actions, uses the same panel/menu/rating
callbacks, and obeys actual readiness/deadline gates. It repeats three dummy
Commits, one Don't know, and one timeout, chooses menu option 1, and selects the
rating scale midpoint. It does not inspect correct answers. The optional
`-simulationDummyResponses` flag arms the same driver immediately and must not be
used when recording an initial manual-input check.

The player requests 48 kHz and a 512-frame Unity DSP buffer for this process and
checks the actual returned format. This is not an OS route change or an acoustic
calibration. Actual scheduled audio, output callbacks, delivered-sample coverage,
and completion are recorded. `SIMULATION_DELIVERY_OBSERVED` includes explicitly
named software output estimates; acoustic onset and uncertainty remain null,
and exposure derivation remains uncertain/consumed. Callback timing cannot be
promoted to microphone-measured onset. Response-window anchors and any response
time derived from them in this run are software timing only, even where the
existing trial CSV uses a shared anchor/RT column. They are not calibrated
acoustic reaction times and cannot enter participant analyses as such.

The simulation source gate requires actual progressing local receipt within
250 ms, actual rendered/latest neutral matching, and a separate actual command
reset acknowledgment. It never grants `SourceFresh` remote-clock qualification.
View events record native view commands and text hashes, not physical capture or
legibility. Screen recordings and captures must be separately retained.

## Evidence and completion

Retain the actual build inventory, raw input pins, OS/process result, console
audit, command journal, joined journal, raw data segments, frame evidence, menu
ledger where applicable, and immutable export bundle. Native completion requires
the durable visit history, full retained tail, completed forms, successful cleanup,
and export. `-simulationQuitOnComplete` exits nonzero if cleanup or export fails.
An inventory flag alone cannot establish completion.

Run the independent native reconciliation tool against those actual artifacts.
It must distinguish software callback observations from acoustic evidence and
identify unexecuted visits, faults, or captures. Preserve failed attempts rather
than replacing their evidence. All seven planned fault cases and the complete
active/yoked history remain required for issue #81; a compilation or isolated
unit test does not satisfy those native run requirements.
