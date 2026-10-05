# Isaac integration

Python integration for the pinned simulator. Phase 1 experiments live in
`spikes/`. The owner authorized Phase 2 development with the G1 in Isaac Sim;
that authorization does not turn outstanding qualification checks into passes.

The Phase 2 backend is built in stacked, unmerged issue branches:

- `workcell/`: deterministic public layout, USD generator and typed accessors (#52).
- `reset/`: hash-verified neutral snapshot and measured readback verification (#53).
- `publisher/`: public scene-only WebSocket state, health and timing evidence (#54).

See [publisher contract](../docs/isaac/publisher.md) and
[rate-test runbook](../docs/spikes/O5.2.3-runbook.md).
The related [reset contract](../docs/isaac/reset.md) and
[private command API](https://github.com/ATR-Lab/acoustic-vocabularies/pull/112) are supplied by #53 and #55.
Fetched assets, generated USD, station configurations and raw evidence stay in
ignored local storage. Generated scene assets must not be committed as ordinary
Git blobs while repository LFS service is unavailable.
