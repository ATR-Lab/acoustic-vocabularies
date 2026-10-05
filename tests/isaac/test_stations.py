"""Synthetic station fixtures; no hardware, accounts or network configuration."""
from copy import deepcopy
import json

import pytest

from isaac.stations.config import validate, validate_fleet, canonical_bytes, digest, load, cyclone_xml, container_plan
from isaac.stations.isolation import check_namespace
from isaac.stations.audit import run_cross_talk, command_matrix
from isaac.publisher.protocol import PublicRegistry, StateEncoder


def config(number=1):
    return dict(version=1, station_id='engineering-'+str(number), logical_host='fixture-host', gpu_index=number-1,
        publisher_port=20000+number*2, command_port=20001+number*2, dds_domain_id=40+number, ros_domain_id=None,
        allowed_client='127.0.0.1', allowed_uid=10000+number, scene_sha256='a'*64, reset_snapshot_sha256='b'*64,
        layout_sha256='c'*64, image_digest='sha256:'+'d'*64, source_revision='e'*40,
        publisher_hz=30, network_mode='none', ipc_mode='private', unitree_dds_enabled=False)


@pytest.mark.parametrize('field,value', [('network_mode','host'), ('ipc_mode','host'), ('unitree_dds_enabled',True),
    ('allowed_client','192.0.2.1'), ('allowed_uid',0), ('gpu_index',True), ('dds_domain_id',233),
    ('source_revision','main'), ('image_digest','image:latest'), ('publisher_hz',45)])
def test_config_rejects_unsafe_or_unpinned_station(field, value):
    with pytest.raises(ValueError):
        validate({**config(), field:value})


@pytest.mark.parametrize('field', ['station_id','publisher_port','dds_domain_id','allowed_uid'])
def test_fleet_rejects_identity_endpoint_domain_or_peer_reuse(field):
    a, b = config(1), config(2)
    b[field] = a[field]
    with pytest.raises(ValueError):
        validate_fleet([a,b])


def test_hash_and_canonical_configuration_binding(tmp_path):
    value = config()
    path = tmp_path/'station.json'
    path.write_bytes(canonical_bytes(value))
    assert load(path, digest(value)) == value
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError, match='hash'):
        load(path, digest(value))


def test_station_identity_matches_public_and_foundation_grammar():
    assert validate({**config(), 'station_id':'s.'+'a'*78})['station_id'] == 's.'+'a'*78
    with pytest.raises(ValueError):
        validate({**config(), 'station_id':'s'*81})


def test_reviewable_plan_never_publishes_ports_or_uses_host_namespace(tmp_path):
    value = config()
    plan = container_plan(value, config_file=tmp_path/'c.json', config_sha256=digest(value),
                          source_directory=tmp_path/'source', output_directory=tmp_path/'output',
                          entrypoint='isaac/stations/reviewed_service.py')
    argv = plan['argv']
    assert argv[argv.index('--network')+1] == 'none'
    assert argv[argv.index('--ipc')+1] == 'private'
    assert argv[argv.index('--user')+1] == '10001:10001'
    assert '--privileged' not in argv and '-p' not in argv and '--publish' not in argv
    assert not plan['executed'] and not plan['gateway_implemented'] and not plan['runtime_entrypoint_validated']
    assert '<NetworkInterface name="lo" multicast="false"/>' in cyclone_xml(value)
    assert '<AllowMulticast>false</AllowMulticast>' in cyclone_xml(value)


def v6(destination='0'*32, prefix='00', flags='00200200', interface='lo'):
    return f'{destination} {prefix} '+('0'*32)+' 00 '+('0'*32)+f' ffffffff 00000001 00000000 {flags} {interface}\n'


def test_actual_linux_unreachable_default_shape_is_not_a_usable_route():
    routes=v6()+v6('0'*31+'1','80','80200001')+v6()
    result=check_namespace(interfaces=['lo'],ipv4_routes='Iface Destination\n',ipv6_routes=routes)
    assert result['namespace_route_check_passed'] and result['ipv6_unreachable_default_entries']==2
    assert not result['packet_capture_complete']


@pytest.mark.parametrize('interfaces,ipv4,ipv6', [(['lo','eth0'],'Iface\n',''),
    (['lo'],'Iface\neth0 00000000\n',''), (['lo'],'Iface\n',v6(flags='00000001')),
    (['lo'],'Iface\n',v6('f'*32,'80','00000001'))])
def test_routable_namespace_is_rejected(interfaces, ipv4, ipv6):
    with pytest.raises(ValueError):
        check_namespace(interfaces=interfaces,ipv4_routes=ipv4,ipv6_routes=ipv6)


class Client:
    def __init__(self, value):
        self.registry=PublicRegistry(value['station_id'],value['scene_sha256'],value['reset_snapshot_sha256'],
            tuple('j'+str(i) for i in range(43)), (('fixture_object',()),))
        self.encoder=StateEncoder(self.registry,source_kind='synthetic')
        self.q=[0.]*43
        self.step=0
        self.other=None
        self.objects={'fixture_object':dict(position_m=[0.,0.,0.],rotation_xyzw=[0.,0.,0.,1.],visible=True,enabled=True,state={})}

    def public_state(self):
        self.step+=1
        return self.encoder.build(self.q,self.objects,self.step/30,self.step)

    def command(self, name, args):
        if name in ('reset','hold_neutral','stop'):
            self.q=[0.]*43
        if name=='demo':
            self.q[0]=.2
            if self.other: self.other.q[0]=.2
        return dict(accepted=True,reset_ok=True)


def test_four_synthetic_stations_all_commands_and_pairs_stay_separate():
    configurations=[config(i) for i in range(1,5)]
    clients={v['station_id']:Client(v) for v in configurations}
    events=[]
    report=run_cross_talk(configurations,clients,events.append)
    assert report['passed'] and report['source_kind']=='synthetic'
    assert report['cases']==4*len(command_matrix()) and report['changed_other_count']==0
    assert report['wrong_station_rejected']==12
    assert not report['wrong_station_connection_authentication_tested'] and not report['one_hour_capacity_run_complete']


def test_cross_talk_is_reported_and_not_erased_by_following_reset():
    configurations=[config(1),config(2)]
    clients={v['station_id']:Client(v) for v in configurations}
    clients['engineering-1'].other=clients['engineering-2']
    events=[]
    report=run_cross_talk(configurations,clients,events.append)
    assert not report['passed'] and report['changed_other_count']==1
    assert events[-1]['changed_stations']==['engineering-2']
    assert clients['engineering-2'].q[0]==.2


def test_live_endpoint_snapshots_never_claim_continuous_isolation():
    configurations=[config(1),config(2)]
    clients={v['station_id']:Client(v) for v in configurations}
    for client in clients.values():
        client.encoder=StateEncoder(client.registry,source_kind='live')
    report=run_cross_talk(configurations,clients,lambda event:None)
    assert report['snapshot_contract_passed'] and not report['passed']
    assert not report['continuous_trace_complete']
    assert report['evidence_scope']=='exact_endpoint_snapshots_only'


def test_wrong_endpoint_frame_fails_actual_public_registry():
    configurations=[config(1),config(2)]
    clients={v['station_id']:Client(v) for v in configurations}
    clients['engineering-1'].public_state=clients['engineering-2'].public_state
    with pytest.raises(ValueError, match='station'):
        run_cross_talk(configurations,clients,lambda event:None)
