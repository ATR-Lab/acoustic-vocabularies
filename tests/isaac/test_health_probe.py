"""Read-only probe correlation and unchanged cached-freshness semantics."""
from copy import deepcopy
import json

import pytest

from isaac.commands.health_probe import reply
from isaac.commands.protocol import decode
from isaac.commands.queue import CommandQueue


SESSION = "a" * 32
IDENTIFIER = "b" * 32


def request():
    return dict(version=1, kind="private_health_probe", control_session_id=SESSION,
                request_id=IDENTIFIER)


def status():
    return dict(control_session_id=SESSION, mode="teaching", paused=False, stopped=False,
                fault=None, demo_active=False, publisher_ready=False,
                neutral_verification_age_ms=301., publisher_age_ms=302.,
                health_sample_host_mono_ms=1000., exposure_ready=False,
                public_stream_recovered=False)


def test_stale_health_is_returned_without_freshening_or_shared_mutable_values():
    original=status();before=deepcopy(original)
    result=reply(request(),SESSION,lambda:original)
    assert set(result)=={"version","kind","control_session_id","request_id","accepted","reason","health"}
    assert result==dict(version=1,kind="private_health_reply",control_session_id=SESSION,
                       request_id=IDENTIFIER,accepted=True,reason="HEALTH",health=before)
    result["health"]["publisher_ready"]=True
    assert original==before


@pytest.mark.parametrize("change",[
    {"version":True},{"version":1.0},{"version":2},{"kind":"private_command"},
    {"command":"reset"},{"args":{}},{"action":"ADD_ONE"},{"target":"tray_A"},
    {"request_id":"B"*32},{"request_id":None},{"control_session_id":"short"},
])
def test_closed_probe_schema_refuses_before_reading_health(change):
    def forbidden():raise AssertionError("Refused probe touched health")
    value=request();value.update(change)
    result=reply(value,SESSION,forbidden)
    assert result["accepted"] is False and result["reason"]=="MALFORMED_PROBE"
    assert result["health"] is None


def test_missing_fields_and_wrong_session_refuse_without_health_disclosure():
    def forbidden():raise AssertionError("Refused probe touched health")
    for key in request():
        value=request();del value[key]
        assert reply(value,SESSION,forbidden)["reason"]=="MALFORMED_PROBE"
    value=request();value["control_session_id"]="c"*32
    result=reply(value,SESSION,forbidden)
    assert result["reason"]=="CONTROL_SESSION_MISMATCH" and result["health"] is None
    assert result["request_id"]==IDENTIFIER and result["control_session_id"]==SESSION


def test_provider_session_change_cannot_be_advertised_under_the_pin():
    result=reply(request(),SESSION,lambda:{**status(),"control_session_id":"c"*32})
    assert result["reason"]=="HEALTH_SESSION_CHANGED" and result["health"] is None
    assert result["accepted"] is False


def test_provider_failure_has_no_successful_fallback():
    def failed():raise RuntimeError("Unavailable")
    with pytest.raises(RuntimeError,match="Unavailable"):reply(request(),SESSION,failed)


@pytest.mark.parametrize("raw",[
    '{"kind":"private_health_probe","kind":"private_health_probe"}',
    '{"kind":"private_health_probe","version":NaN}',
    '{"kind":"private_health_probe","version":1e999}',
    b'{"kind":"private_health_probe"}',
    ' '*16385,
])
def test_shared_wire_decoder_rejects_duplicates_nonfinite_binary_and_oversize(raw):
    with pytest.raises(ValueError):decode(raw)


def test_replayed_probe_ages_existing_health_without_owner_drain(monkeypatch):
    now=[1_000_000_000]
    monkeypatch.setattr("isaac.commands.queue.time.monotonic_ns",lambda:now[0])
    initial={**status(),"neutral_verification_age_ms":10.,"publisher_age_ms":20.,
             "publisher_ready":True,"exposure_ready":True}
    class NoSimulatorDispatcher:
        reset_manager=type("Reset",(),{"adapter":type("Adapter",(),{"sim_time":0.})()})()
        def _thread(self):pass
        def health(self):return deepcopy(initial)
    handoff=CommandQueue(NoSimulatorDispatcher())
    first=reply(request(),SESSION,handoff.health)
    now[0]+=300_000_000
    second=reply(request(),SESSION,handoff.health)
    assert second["health"]["health_sample_host_mono_ms"]>first["health"]["health_sample_host_mono_ms"]
    assert second["health"]["neutral_verification_age_ms"]==310.
    assert second["health"]["publisher_age_ms"]==320.
    assert second["health"]["publisher_ready"] is False
    assert second["health"]["exposure_ready"] is False
    assert handoff.queue.empty() and handoff.sequence==0
