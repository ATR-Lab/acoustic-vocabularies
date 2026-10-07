"""Injected shutdown failures must not strand independent diagnostic resources."""
import subprocess
from types import SimpleNamespace

import pytest

from isaac.e2e.diagnostic import close_phase_resources
from isaac.e2e.same_iteration import StageMutationWatch


@pytest.mark.parametrize('failed_stop', [False, True])
@pytest.mark.parametrize('failed_terminate', [False, True])
def test_all_cleanup_attempted_despite_marker_terminate_and_final_wait_failures(failed_stop,failed_terminate):
    events=[]; errors=[]
    def wait(timeout):
        events.append(('wait',timeout));raise subprocess.TimeoutExpired('owned-client',timeout)
    def terminate():
        events.append('terminate')
        if failed_terminate:raise OSError('injected terminate failure')
    def stop(path,data):
        events.append(('stop',path,data))
        if failed_stop:raise OSError('injected marker failure')
    def broken_close():
        events.append('broken closer');raise OSError('injected writer failure')
    process=SimpleNamespace(wait=wait,terminate=terminate)
    resources=[('writer',SimpleNamespace(close=broken_close)),
        ('notice',SimpleNamespace(close=lambda:events.append('notice closed'))),
        ('socket',SimpleNamespace(close=lambda:events.append('socket closed')))]
    close_phase_resources(process,'private/stop',stop,resources,errors)
    assert events==[('stop','private/stop',b'stop'),('wait',5),'terminate',('wait',5),
        'broken closer','notice closed','socket closed']
    assert len(errors)==3+failed_stop+failed_terminate
    assert any(item.startswith('client final wait:') for item in errors)
    assert any(item.startswith('writer:') for item in errors)


def test_normal_shutdown_does_not_terminate_or_report_successful_resources_as_failed():
    events=[];errors=[]
    process=SimpleNamespace(wait=lambda timeout:events.append('exited'),
        terminate=lambda:pytest.fail('Already exited process must not be terminated'))
    close_phase_resources(process,'stop',lambda *_:events.append('stop'),
        [('writer',SimpleNamespace(close=lambda:events.append('closed')))],errors)
    assert events==['stop','exited','closed'] and errors==[]


def test_notice_revocation_continues_and_failed_handle_can_be_retried():
    events=[];broken=[True]
    def revoke_first():
        events.append('first')
        if broken[0]:raise RuntimeError('injected revoke failure')
    first=SimpleNamespace(Revoke=revoke_first)
    second=SimpleNamespace(Revoke=lambda:events.append('second'))
    watch=object.__new__(StageMutationWatch)
    watch._notices=[first,second];watch._closed=False
    watch.invalidate=lambda:events.append('invalidated')
    with pytest.raises(RuntimeError,match='injected revoke'):
        watch.close()
    assert watch._closed and events==['invalidated','first','second'] and watch._notices==[first]
    broken[0]=False;watch.close()
    assert events==['invalidated','first','second','invalidated','first'] and watch._notices==[]
