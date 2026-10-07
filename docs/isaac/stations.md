# Station isolation development (#57)

This change provides a strict private station manifest, namespace checks and a
cross-talk harness. It does **not** claim that four or six participant stations
have been provisioned, run for an hour, or reviewed for network isolation. The
single-process-per-station model is a development proposal while ADR-003 and
hardware capacity remain unresolved. No host firewall, switch/AP, route, user
account or physical-robot network was changed.

## Private configuration and restart identity

`isaac.stations.config` requires closed versioned fields for logical station and
host, GPU index, distinct public/private ports, DDS/ROS domain, allowed loopback
client and provisioned Unix UID, scene/layout/neutral hashes, a full source
revision, a pinned image digest, publisher rate and namespace policy. Configs
use canonical UTF-8 JSON and LF; JSON is also the supported YAML 1.2 subset if an
operator chooses a `.yaml` suffix. Loading checks the configured raw SHA-256.
Do not silently rebuild a config after a mismatch or recapture a snapshot.

A fleet rejects duplicate identities, host ports, domains or gateway UIDs.
Separate ports alone are insufficient authentication: a same-UID process can
otherwise access multiple mode 0600 sockets. Dedicated station gateway accounts
and socket ownership are required before deployment. This code does not create
those accounts. A headset/Unity registry must still require its configured
station, scene and snapshot identity; a private control-session ID is an
idempotency epoch and must not be described as a secret credential.

Validate a private manifest containing basenames and expected SHA-256 values:

```text
python -m isaac.stations --manifest <private-directory>/fleet.json
```

The command reads files and reports count/hashes only. It starts no services.
The example under `apparatus/stations/` uses deliberately synthetic hashes and
logical identifiers; it is a fixture, not a deployment manifest.

## Containment and reviewed launch plans

`container_plan` returns an argv array for review. It pins the image, GPU and
configuration hash, mounts source/config read-only, and selects `--network none`,
`--ipc private`, dropped capabilities, no new privileges and an explicit
non-root station UID. It never executes Docker or publishes a port. Output must
be outside the read-only source tree. Existing asset/cache mounts, a reviewed
long-running service entrypoint and per-station Unix gateways are intentionally
not fabricated; the returned plan flags them as unvalidated. Do not install a
service unit that points to an unfinished entrypoint. A complete supervised
launcher, start/stop/restart units and actual station gateway integration remain
open work.

Docker's [none network driver](https://docs.docker.com/engine/network/drivers/none/)
isolates the container network stack. Each station also has private IPC, so a
ROS sidecar may share only its own station's namespace, never another station's
or the host's. Unitree DDS stays disabled in this development path. A generated
CycloneDDS XML template selects `lo`, disables multicast and limits discovery
to 127.0.0.1; it is passed as `CYCLONEDDS_URI`. This follows the
[CycloneDDS 0.10.5 interface configuration](https://cyclonedds.io/docs/cyclonedds/0.10.5/config/cyclonedds_specifics.html).
The deployed middleware's exact version and XML acceptance still require a
runtime check; no DDS packet measurement is inferred from a generated file.

`check_namespace` reads interface/route state and rejects non-loopback interfaces
or usable routes. A read-only check in the approved network-none container found
Linux's two unreachable IPv6 default entries (`RTF_REJECT`) on `lo`; the validator
records those separately instead of mistaking them for usable default routes.
The actual allowed IPv6 host route is `::1/128`; IPv4 has no routes. This check is
namespace evidence, not switch/AP firewall sign-off or a packet capture.

Before any station-network deployment, the network owner must provide fixed IPs,
gateway mappings and a reviewed firewall/switch policy. Capture packet counts
on every relevant egress interface during the full run, retain payloads privately
if capture is authorized, and publish only counts. Do not infer zero physical
robot traffic from an unobserved interface or from a port-range assumption.

## Cross-talk and wrong-station harness

`run_cross_talk(configurations, clients, durable_event_sink)` accepts explicit
provisioned client adapters. Each has a strict `PublicRegistry`,
`public_state()` and synchronous `command(name,args)` returning the terminal
reply. The test resets every station, binds every received frame to the expected
station/scene/neutral identity, and executes every command type plus all 32 demos
on one station at a time. Before and after each command it hashes every other
station's complete public joints/objects. Any change stops the test immediately
and preserves the failed event without a cleanup reset that could conceal it.
These are exact endpoint snapshots: there is no tolerance that could hide a
small change, and physical jitter conservatively fails this narrow contract.
A transient excursion that returns before the command reply is not observed.
Live reports therefore keep `passed=false` even when
`snapshot_contract_passed=true`; a continuous independent per-station trace
with measured coverage and an explicit physical tolerance is still required.

The wrong-station test intentionally validates another station's full frame
against the intended registry. It proves application-level identity refusal,
not socket-level authentication. The report makes that distinction explicit.
Client authentication, namespace/process separation and physical routing need
their own deployment evidence.

The 26 pure tests include four synthetic stations, all command types and 32 legal
pairs per station, all 12 directed wrong-station mappings, an injected shared-state
bug, unsafe config rejection, config/hash binding and actual Linux route shapes.
They are not four Isaac processes. GPU/driver inventory, peak VRAM and step-time
CSV under the final N-station load, one-hour stability, actual cross-talk,
packet capture, restart neutrality and independent network review remain pending.
