# Separate diagnostic receiver

The original hour and disconnect measurements used `LocalCollector` inside the simulator process. Bounded profiling found 80.88 physics steps/s without a receiver, 68.76 with that strict receiver in process, and 79.49 with the same strict validation in an ordinary Python subprocess. These short diagnostics do not replace the recorded failed hour or disconnect result.

Both `run_publisher_check(...)` and `run_disconnect_check(...)` now accept `collector_mode="process"`. The default remains `"thread"` for reproducibility. Only the diagnostic harness changes: publisher sampling, scheduling, serialization, transport, physics stepping, rate thresholds and frame contract remain unchanged. Metadata explicitly records the selected mode.

`ProcessCollector` starts the current Python executable with a standalone module, without initializing Kit/Isaac. The child uses the same `strict_loads` and `validate_frame` functions on every frame and retains the original sequence-gap counting. It reports only small count/error messages to the parent; full first/last frames are copied back after child shutdown. The simulation thread never waits on state delivery. Setup precedes the hour's measurement; the disconnect schedule records actual connection times and continues stepping during child startup and shutdown.

A temporary private directory holds the public registry and child result. Shutdown waits for the child, retains validation errors and first/last frames, closes pipes and removes that owned directory. An unexpected child exit is a failed diagnostic, not successful completion. The receiver does not expose a listener, private command, clock qualification or participant flow.

Run the synthetic subprocess/Unix-socket checks in the approved Linux image with its existing `websockets` 12:

```bash
PYTHONPATH=/repo /isaac-sim/python.sh -m unittest discover -s /repo/tests/isaac -p test_process_collector.py -v
```

Four tests cover exact validated delivery/first-last samples/cleanup, preserved sequence-gap accounting, strict schema rejection and unexpected-child fault propagation. They passed in the approved network-isolated image. Windows skips these Unix tests; 43 existing pure publisher tests pass there. The next actual run must retain both the original failure and new receiver mode, source hashes, CSVs, summaries and unchanged acceptance screens.
