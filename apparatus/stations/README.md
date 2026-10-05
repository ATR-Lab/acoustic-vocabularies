# Private station manifests

Copy the synthetic fixture only into ignored private storage, replace every
fixture value with a reviewed deployment value, serialize with
`isaac.stations.config.canonical_bytes`, and record its SHA-256 in a private fleet
manifest. Do not commit real station IDs, IPs, host paths, allowed user identities
or network topology. The fixture is not an operational service configuration.

See `docs/isaac/stations.md` for validation, loopback namespace policy, distinct
gateway UID requirements, cross-talk diagnostics and outstanding network review.
