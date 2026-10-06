# Versioning

Deployables use semantic tags: `unity-vX.Y.Z`, `isaac-vX.Y.Z`,
`console-vX.Y.Z`, `sound-vX.Y.Z`. Release notes record the exact commit,
`apparatus_version`, dependency/asset hashes, and the protocol version.
An untagged spike is not a study build. Breaking contracts increase the major
version; compatible additions increase minor; corrections increase patch.

Every event/log row includes `protocol_version`, `apparatus_version` and
`schema_version`. Exact trial/exposure/deviation field mapping waits for the
external read-only templates. Do not silently treat fixture schemas as protocol
schemas. A missing protocol version prevents a study release.

The apparatus version names the frozen combination of software, assets, hardware,
rates and calibration. After freeze, any necessary change starts a documented
new apparatus version, with reason, affected records and validation. No silent
upgrades. Never move a published tag. Record SHA-256 at session exit and in the
apparatus manifest. Keep station identifiers in private configuration.

G1 accepts architecture only with recorded review and real spike evidence.
Phase 2 remains blocked until the gate owner signs off. Release work does not
authorize merging: maintainers review and merge all post-skeleton pull requests.
