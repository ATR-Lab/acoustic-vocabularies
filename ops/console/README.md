# Operator console

Separate loopback web display for issue #73. Uses the approved development
Python environment; no additional web dependency, remote asset or network change.

```powershell
python -m ops.console.server --demo --audit .local/console-demo/audit.local.jsonl --port 8769
```

Open `http://127.0.0.1:8769`. The labelled DEMO has five synthetic slots and cannot
play audio, drive Isaac or bind a non-DEMO visit. Its three-second illustrative
progress is not study timing evidence. Normal operation requires `--config`
instead of `--demo` and a separately configured real engine.

The **Orientation eligibility** panel shows each configured orientation receipt
before allocation: outcome, receipt hash, verification status and whether
allocation handoff is blocked. It reuses the `av_schedules.admission` reader;
visit load and start stay blocked unless that receipt verifies as a pass. The DEMO
has no receipts. See the contract's pre-allocation section for configuration.

The [runbook](../../docs/spikes/O5.6.2-runbook.md) specifies private provisioning
and pending paired headset capture. The [contract](../../docs/interfaces/operator-console.md)
defines the same-PC Link mailbox. An adapter does not create a complete session
host or replace package, reset, route and input authorities. LAN/standalone
integration and physical qualification remain pending.
