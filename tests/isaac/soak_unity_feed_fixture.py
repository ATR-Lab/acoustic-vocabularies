"""Regenerate the driver half of the Unity soak-feed fixture (synthetic only).

The fake command server wraps the real dispatcher and durable command log; the
wifi_drop fault hook only writes the operator recovery file. Nothing here touches
a station, network or Isaac process. The Unity half (native journal and data
journal) is produced separately by the Unity EditMode exporter from these exact
feed bytes; see ``unity/Assets/Tests/SessionIntegration/SoakFeedFixtureTests.cs``.

    python tests/isaac/soak_unity_feed_fixture.py OUTPUT_DIRECTORY
"""
from __future__ import annotations

import sys
from pathlib import Path

PARAMS = dict(station_id='station-01', seed=527, seconds=240, block_seconds=60, trial_interval_s=20,
              fault_types=['wifi_drop'])
FILES = ('schedule.json', 'driver-journal.jsonl', 'unity-inputs.jsonl', 'command-0.jsonl')


def generate(output):
    from isaac.reset.snapshot import canonical_bytes
    from test_soak_driver import Clock, FakeServer, schedule_bytes
    from isaac.soak.driver import SoakDriver

    output = Path(output)
    output.mkdir(parents=True)
    work = output / 'work'
    clock = Clock()
    server = FakeServer(work, clock)

    def inject(request):
        # Operator stand-in for the fixture only: confirm recovery after 30 s.
        clock.advance(30)
        (work / 'driver' / 'operator' / f"recovery-{request['fault_id']}.json").write_bytes(canonical_bytes(
            dict(version=1, fault_id=request['fault_id'], control_session_id=server.session, operator_initiated=True)))
        return dict(method='unit_fixture')

    raw, pin = schedule_bytes(**PARAMS)
    result = SoakDriver(raw, pin, work / 'driver', server, control_session_id=server.session, clock=clock,
                        sleep=clock.sleep, fault_hook=inject, authorize_fault_injection=True).run()
    assert result['completed'], result
    logs = server.command_logs()
    assert len(logs) == 1
    (output / 'schedule.json').write_bytes(raw)
    for name in ('driver-journal.jsonl', 'unity-inputs.jsonl'):
        (output / name).write_bytes((work / 'driver' / name).read_bytes())
    (output / 'command-0.jsonl').write_bytes(logs[0])
    return pin


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root), str(Path(__file__).resolve().parent)]
    print(generate(sys.argv[1]))
