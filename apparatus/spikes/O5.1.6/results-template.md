# O5.1.6 audio onset results

Status: **Needs operator run. No route measured or chosen.**

| Route | Mode | Capture | Requested / matched / missing / ambiguous / extra | Mean offset ms | Residual SD / p95 / max ms | Independent sync bound ms | Combined screen ms | Underruns | Buffer / Hz | 20 ms conclusion |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Headset speakers | Plain / scheduled | Acoustic | Pending | | | | | Unknown | | Pending |
| Headset wired | Plain / scheduled | Acoustic + electrical separately | Pending | | | | | Unknown | | Pending |
| PC headphones, Link | Plain / scheduled | Acoustic + electrical separately | Pending | | | | | Unknown | | Pending |

Record setup equipment photo (no people/identifiers), capture channels and fixed thresholds, independently calibrated sync instrumentation + error bound, check-anchor residuals, volume setting, room noise, substitutions, notifications/volume lock, platform underrun diagnostics, and app/build revision.

Explain acoustic minus electrical offset (transducer/air/coupler plus measurement channel delay). Do not subtract these paths without measuring channel skew. A photodiode identifies display onset, not audible onset. State why the independent sync calibration measures display command-to-photon delay if that delay enters the mapping.

ADR-005 recommendation: pending actual measurements. Engineering screening adds acoustic p95 absolute residual to measured worst check-anchor error and independent instrumentation bound. This is conservative evidence for review, not a confidence interval or proof of population 95% coverage. Repeated sessions and drift validation are needed before response-time claims.

Public evidence after privacy review: `docs/spikes/audio/onsets_<route>_<mode>_<capture-kind>.csv`, summary JSON and settings without station identities. Keep raw WAVs, photographs with metadata and local paths under ignored `local-data/` until reviewed.
