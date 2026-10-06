import argparse, json, os, socket, sys, threading, time, uuid
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--out', type=Path, required=True)
p.add_argument('--load', action='store_true')
p.add_argument('--fixed-nodelay', type=int, choices=(0, 1))
p.add_argument('--phases', type=int, choices=(1, 2, 4), default=4)
a = p.parse_args()
sys.path.insert(0, str(Path.cwd() / 'spikes/O5.1.5'))
from collect_loopback import LoopbackWebSocket
assert not a.out.exists()
load_stop = threading.Event()
load_rows = []
load_errors = []

def load():
    c = None
    try:
        c = LoopbackWebSocket('ws://127.0.0.1:18865/load')
        while not load_stop.is_set():
            msg = c.receive()
            assert len(msg) == 10799
            load_rows.append(time.perf_counter_ns())
    except Exception as error:
        load_errors.append(type(error).__name__)
    finally:
        if c:
            try:
                c.close()
            except Exception:
                pass
worker = threading.Thread(target=load, daemon=True) if a.load else None
if worker:
    worker.start()
rows = []
started = time.perf_counter_ns()
try:
    choices = (0, 1, 1, 0) if a.fixed_nodelay is None else (a.fixed_nodelay,) * a.phases
    for phase, nodelay in enumerate(choices):
        client = None
        for index in range(200):
            if time.perf_counter_ns() - started > 150000000000:
                raise TimeoutError('total bound')
            if client is None:
                client = LoopbackWebSocket('ws://127.0.0.1:18867/echo')
                client.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, nodelay)
                read = client.read

                def bounded(n, c=client, r=read):
                    left = c.deadline - time.perf_counter()
                    if left <= 0:
                        raise TimeoutError('200ms')
                    c.socket.settimeout(left)
                    return r(n)
                client.read = bounded
            request = {'kind': 'diagnostic_echo', 'request_id': uuid.uuid4().hex}
            sent = time.perf_counter_ns()
            client.deadline = sent / 1000000000.0 + 0.2
            row = dict(phase=phase, nodelay=client.socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY), index=index, request_id=request['request_id'], sent_client_ns=sent)
            try:
                client.send(json.dumps(request))
                reply = json.loads(client.receive())
                received = time.perf_counter_ns()
                assert reply['kind'] == 'diagnostic_echo' and reply['request_id'] == request['request_id']
                row.update(received_client_ns=received, rtt_ms=(received - sent) / 1000000.0, within_200ms=received - sent <= 200000000)
            except Exception as error:
                row.update(error=type(error).__name__, failure_client_ns=time.perf_counter_ns(), within_200ms=False)
                try:
                    client.close()
                except Exception:
                    pass
                client = None
            rows.append(row)
            time.sleep(max(0.001, 0.075 - (time.perf_counter_ns() - sent) / 1000000000.0))
        if client:
            try:
                client.close()
            except Exception:
                pass
finally:
    load_stop.set()
    if worker:
        worker.join(3)
    report = dict(scope='Synthetic echo through Linux relay and Windows OpenSSH; not native client/source', participant=False, qualification=False, load=a.load, load_thread_alive=worker.is_alive() if worker else False, load_errors=load_errors, load_receive_client_ns=load_rows, elapsed_seconds=(time.perf_counter_ns() - started) / 1000000000.0, rows=rows)
    with a.out.open('x') as f:
        json.dump(report, f)
        f.flush()
        os.fsync(f.fileno())
print(json.dumps({'rows': len(rows), 'failures': sum((not r['within_200ms'] for r in rows)), 'load_frames': len(load_rows), 'load_errors': load_errors}))
