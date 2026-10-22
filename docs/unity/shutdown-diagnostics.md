# Quit-time shutdown and dump capture

Refs #150, #148 and #82. This change bounds and records quit-time teardown. It
does not explain or fix the 005-full-002 exit `0xC0000005`, and it does not
replace a native manual-close rerun with independent process receipts.

## Quit coordinator

`JoinedEngineeringBootstrap` now registers one run-once coordinator on
`Application.quitting`. `Application.wantsToQuit` only leaves a breadcrumb and
always returns true, so quit is never blocked, delayed or cancelled. The order
is:

1. `joined_close`: the same `JOIN_HOST_DISABLED` fault that scene teardown
   previously raised for a live visit, then the existing `Close()` (mailbox,
   staged modules and their private control clients, session owner, frame
   capture, visit resources, and for simulation builds the export and
   `native-result` receipt). Status codes and receipt semantics are unchanged.
2. `state_source_shutdown`: the state host's existing disable fault, public
   state socket cancellation and journal finalization, while every Unity object
   still exists. `OnDestroy` remains an idempotent fallback.
3. One bounded worker drain: at most 500 ms in total, not per worker, for the
   tracked workers (`private_control_socket`, `public_state_socket`,
   `state_source_journal_writer`). Each is recorded as `completed`,
   `cancelled`, `faulted` (exception type only) or `timed_out`. A timed-out
   worker is left to the runtime and its eventual fault is observed.

A stage that throws is recorded and later stages still run. The coordinator
never sets, suppresses or replaces the process exit code; a nonzero exit after
a successful receipt remains `NATIVE_PROCESS_NOT_SUCCESSFUL` in reconciliation.

Client `Dispose()` still never waits on the Unity thread. Cancellation and
`Abort()` release pending socket IO; the quit drain is the only wait. The
journal keeps its 2 s writer join and records whether the writer stopped. The
frame watchdog timer is disposed with a 250 ms bounded wait for in-flight
callbacks, never while holding the timing lock. The audio source releases its
clip before prepared clips are destroyed. Health semantics are unchanged: the
250 ms effective-age bound, strict session/request validation and the
post-persistence gate are untouched.

## Breadcrumbs

Each stage, each `Close()` step (`close_step_begin`/`close_step_end`), the
simulation closure, the journal writer outcome, the frame watchdog stop and
each worker outcome writes one JSON line:

```
SHUTDOWN_BREADCRUMB {"event":"shutdown_stage","sequence":N,"stage":"...","local_mono_ms":...,"managed_thread":...,"process_id":...,"detail":{...}}
```

Lines go to the Unity player log. From `Application.quitting` onward they are
also written through (`FileOptions.WriteThrough`, flushed per line) to
`<persistentDataPath>/operator-logs/shutdown-<guid>.local.jsonl`. They hold
bounded codes, counts and monotonic times only: no paths, endpoints, payloads
or exception text. The last line before an abnormal exit bounds where teardown
had reached. `quit_ready` with every worker `completed` means no tracked
managed network or journal worker was still running when managed quit
finished; it does not exclude native, plugin, OpenXR or audio teardown.

## Capturing a dump on the next native attempt

No dump capture is configured by this change. The operator chooses one method
per attempt, records it in the attempt notes, and keeps dumps private.

Dump folder: a fresh private ignored directory per attempt, for example
`.local\dumps\<attempt-id>\` in the checkout (`.local/` is ignored) or a
private folder outside any repository. Full dumps contain process memory.
Never commit, upload or attach them; record only file name, size and SHA-256 in
the private attempt evidence.

### A. ProcDump attached to the launched player (preferred)

This keeps the existing launcher and its independent process-exit receipt.
After the player starts and its PID is known:

```powershell
procdump.exe -e -ma <player-pid> .local\dumps\<attempt-id>\
```

`-e` writes a dump on an unhandled (second-chance) exception, `-ma` writes a
full memory dump. Do not use `-e 1`: Mono raises first-chance access violations
for ordinary managed null checks, so first-chance dumps are noise. ProcDump
attaches as a debugger and sees exceptions raised during process exit, when
Windows Error Reporting may not run. Debugger attachment slightly changes
timing; record that the attempt was attached.

### B. ProcDump as the launcher

```powershell
procdump.exe -e -ma -x .local\dumps\<attempt-id>\ <path>\experiment.exe <player arguments>
```

ProcDump starts the player and waits for it. The process receipt must then
come from the player itself (ProcDump prints the player's exit code); do not
record ProcDump's own exit code as the player's.

Running ProcDump for the first time requires the operator to accept its
licence (`-accepteula`). That is the operator's decision and is not scripted.

### C. Windows Error Reporting LocalDumps

An administrator may add, for the attempt only:

```
HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\experiment.exe
  DumpFolder  REG_EXPAND_SZ  <private dump folder>
  DumpType    REG_DWORD      2   (full dump)
  DumpCount   REG_DWORD      3
```

This changes a system setting. It is an operator step only: agents must not
apply it. Remove the key after the attempt. WER may not run for a fault that
occurs late in process exit, so absence of a WER dump is not evidence that no
fault occurred. Also check Unity's own crash folder
(`%TEMP%\<company>\<product>\Crashes`).

## Next native evidence

A manual-close rerun of the affected segment should retain: the player log and
breadcrumb file hashes, the cleanup/export receipt, the independently observed
exit code, and either a dump or a statement that the chosen method produced
none. A single successful exit does not prove a fix; the original nonzero
receipt stays unchanged.
