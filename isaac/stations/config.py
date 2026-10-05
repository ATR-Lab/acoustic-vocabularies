"""Strict private station manifests, with explicit per-process isolation."""
import hashlib
import ipaddress
import json
import re
from copy import deepcopy
from pathlib import Path

FIELDS = {'version', 'station_id', 'logical_host', 'gpu_index', 'publisher_port', 'command_port',
          'dds_domain_id', 'ros_domain_id', 'allowed_client', 'allowed_uid', 'scene_sha256',
          'reset_snapshot_sha256', 'layout_sha256', 'image_digest', 'source_revision',
          'publisher_hz', 'network_mode', 'ipc_mode', 'unitree_dds_enabled'}
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validate(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('Exact private station fields required')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported station configuration')
    for key in ('station_id', 'logical_host'):
        if not isinstance(value[key], str) or not ID.fullmatch(value[key]):
            raise ValueError('Safe logical identifiers required')
    for key, lo, hi in (('gpu_index', 0, 63), ('publisher_port', 1024, 65535),
                       ('command_port', 1024, 65535), ('dds_domain_id', 0, 232), ('allowed_uid', 1, 2**31-1)):
        if type(value[key]) is not int or not lo <= value[key] <= hi:
            raise ValueError('Invalid integer field: '+key)
    if value['publisher_port'] == value['command_port']:
        raise ValueError('Public and private endpoints must differ')
    ros = value['ros_domain_id']
    if ros is not None and (type(ros) is not int or not 0 <= ros <= 232):
        raise ValueError('Invalid ROS domain')
    if ros is not None and ros != value['dds_domain_id']:
        raise ValueError('A station ROS domain must agree with its DDS domain')
    try:
        client = ipaddress.ip_address(value['allowed_client'])
    except (ValueError, TypeError):
        raise ValueError('Explicit allowed client address required') from None
    if not client.is_loopback:
        raise ValueError('This development launcher permits loopback gateways only')
    for key in ('scene_sha256', 'reset_snapshot_sha256', 'layout_sha256'):
        if not isinstance(value[key], str) or not HASH.fullmatch(value[key]):
            raise ValueError('Verified asset hash required: '+key)
    if not isinstance(value['image_digest'], str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value['image_digest']):
        raise ValueError('Pinned image ID/digest required')
    if not isinstance(value['source_revision'], str) or not re.fullmatch(r'[0-9a-f]{40}', value['source_revision']):
        raise ValueError('Full reviewed source revision required')
    if type(value['publisher_hz']) is not int or value['publisher_hz'] not in (30, 60):
        raise ValueError('Publisher rate must be30 or60 Hz')
    if value['network_mode'] != 'none' or value['ipc_mode'] != 'private' or value['unitree_dds_enabled'] is not False:
        raise ValueError('Development stations require isolated network/private IPC and no Unitree DDS')
    return deepcopy(value)


def load(path, expected_sha256):
    raw = Path(path).read_bytes()
    if not HASH.fullmatch(expected_sha256) or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Station config hash mismatch')
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result: raise ValueError('Duplicate configuration key')
            result[key] = item
        return result
    value = validate(json.loads(raw, object_pairs_hook=unique))
    if raw != canonical_bytes(value):
        raise ValueError('Canonical UTF8/LF configuration required')
    return value


def validate_fleet(configurations):
    stations = [validate(value) for value in configurations]
    if not stations:
        raise ValueError('At least one explicit station required')
    if len({value['station_id'] for value in stations}) != len(stations):
        raise ValueError('Duplicate station identity')
    ports, domains, users = set(), set(), set()
    for value in stations:
        host = value['logical_host']
        for port in (value['publisher_port'], value['command_port']):
            if (host, port) in ports: raise ValueError('Duplicate host endpoint')
            ports.add((host, port))
        if (host, value['dds_domain_id']) in domains:
            raise ValueError('Duplicate host DDS/ROS domain')
        domains.add((host, value['dds_domain_id']))
        # Same-user Unix peer checks cannot distinguish two station gateways.
        if (host, value['allowed_uid']) in users:
            raise ValueError('Each station gateway needs a distinct provisioned Unix UID')
        users.add((host, value['allowed_uid']))
    return stations


def cyclone_xml(value):
    validate(value)
    return ('<CycloneDDS><Domain Id="'+str(value['dds_domain_id'])+'">'
            '<General><Interfaces><NetworkInterface name="lo" multicast="false"/></Interfaces>'
            '<AllowMulticast>false</AllowMulticast></General>'
            '<Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>32</MaxAutoParticipantIndex>'
            '<Peers><Peer Address="127.0.0.1"/></Peers></Discovery></Domain></CycloneDDS>\n')


def container_plan(value, *, config_file, config_sha256, source_directory, output_directory, entrypoint):
    """Return argv for review; never executes Docker, edits routes or installs users."""
    value = validate(value)
    if config_sha256 != digest(value):
        raise ValueError('Launch configuration hash mismatch')
    if not isinstance(entrypoint, str) or not re.fullmatch(r'isaac/[a-zA-Z0-9_/]+\.py', entrypoint) or '..' in entrypoint:
        raise ValueError('Reviewed source-relative Python entrypoint required')
    paths = [Path(path).resolve() for path in (config_file, source_directory, output_directory)]
    if any(',' in str(path) for path in paths):
        raise ValueError('Docker mount paths must not contain commas')
    config, source, output = paths
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Keep station evidence outside the read-only source checkout')
    argv = ['docker', 'run', '--rm', '--name', 'av-'+value['station_id'], '--network', 'none',
            '--ipc', 'private', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', str(value['allowed_uid'])+':'+str(value['allowed_uid']),
            '--gpus', 'device='+str(value['gpu_index']),
            '--mount', f'type=bind,src={config},dst=/station/config.json,readonly',
            '--mount', f'type=bind,src={source},dst=/source,readonly',
            '--mount', f'type=bind,src={output},dst=/results',
            '--env', 'ROS_LOCALHOST_ONLY=1', '--env', 'PYTHONPATH=/source', '--env', 'HOME=/results/home',
            '--env', 'CYCLONEDDS_URI='+cyclone_xml(value).strip(),
            '--env', 'STATION_CONFIG_SHA256='+config_sha256]
    if value['ros_domain_id'] is not None:
        argv += ['--env', 'ROS_DOMAIN_ID='+str(value['ros_domain_id'])]
    argv += [value['image_digest'], '/isaac-sim/python.sh', '/source/'+entrypoint,
             '--station-config', '/station/config.json', '--output', '/results']
    return dict(station_id=value['station_id'], config_sha256=config_sha256, argv=argv,
                executed=False, independent_process=True, network_mode='none', ipc_mode='private',
                gateway_implemented=False, runtime_entrypoint_validated=False,
                warning='Launch plan only: reviewed service entrypoint, existing asset mounts and per-station UID gateways must be provisioned.')
