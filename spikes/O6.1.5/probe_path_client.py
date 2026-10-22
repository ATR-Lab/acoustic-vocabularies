"""Windows diagnostic client for the private health-probe path (refs #148).

Sends one correlated WebSocket text message at a time with the unchanged
75 ms sequential cadence and a measured 200 ms response bound, and records
client send/first-byte/complete times (Python perf_counter_ns). It is not the
Unity client, grants no exposure permission and is never a fallback.

Transports:
  --tcp PORT      the documented SSH local forward (127.0.0.1:PORT)
  --ssh-stdio P   `ssh -W 127.0.0.1:P mlws`: the same SSH direct-tcpip channel
                  without the Windows loopback listener (comparison only)
Messages:
  --session HEX   private_health_probe for that pinned control session
  (otherwise)     diagnostic_echo for spikes/O6.1.4/network_echo_server.py
"""
import argparse, base64, hashlib, json, os, queue, socket, struct, subprocess, threading, time, uuid
from pathlib import Path


class Stream:
    """Byte stream with an absolute receive deadline (socket or ssh -W pipes)."""
    def __init__(self, tcp=None, stdio=None, host='mlws'):
        self.sock = self.proc = None
        self.chunks, self.buffer = queue.Queue(), b''
        if tcp:
            self.sock = socket.create_connection(('127.0.0.1', tcp), timeout=5)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            source = lambda: self.sock.recv(65536)
        else:
            self.proc = subprocess.Popen(['ssh', '-o', 'BatchMode=yes', '-W', f'127.0.0.1:{stdio}', host],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            source = lambda: self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, 'read1') else os.read(self.proc.stdout.fileno(), 65536)
        def pump():
            try:
                while True:
                    data = source()
                    self.chunks.put((time.perf_counter_ns(), data))
                    if not data: return
            except Exception:
                self.chunks.put((time.perf_counter_ns(), b''))
        threading.Thread(target=pump, daemon=True).start()

    def write(self, data):
        if self.sock: self.sock.sendall(data)
        else: self.proc.stdin.write(data); self.proc.stdin.flush()

    def read(self, n, deadline_ns):
        """Return (bytes, perf_ns of the chunk that completed them)."""
        stamp = time.perf_counter_ns()
        while len(self.buffer) < n:
            left = (deadline_ns - time.perf_counter_ns()) / 1e9 if deadline_ns else 5
            if left <= 0: raise TimeoutError('deadline')
            try: stamp, data = self.chunks.get(timeout=left)
            except queue.Empty: raise TimeoutError('deadline')
            if not data: raise EOFError('closed')
            if self.first is None: self.first = stamp
            self.buffer += data
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out, stamp

    def close(self):
        try:
            if self.sock: self.sock.close()
            else: self.proc.stdin.close(); self.proc.kill(); self.proc.wait(5)
        except Exception: pass


class Client:
    def __init__(self, stream, path):
        self.s = stream; self.s.first = None
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.write((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                      f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        head = b''
        while b'\r\n\r\n' not in head:
            data, _ = self.s.read(1, time.perf_counter_ns() + 5_000_000_000); head += data
            if len(head) > 65536: raise RuntimeError('upgrade')
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if not head.startswith(b'HTTP/1.1 101 ') or accept not in head: raise RuntimeError('upgrade refused')

    def send(self, text, opcode=1):
        payload = text.encode() if isinstance(text, str) else text
        n = len(payload); mask = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80 | (n if n < 126 else 126)]) + (struct.pack('!H', n) if n >= 126 else b'')
        self.s.write(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload)))

    def receive(self, deadline_ns):
        self.s.first = None
        while True:
            (a, b), _ = self.s.read(2, deadline_ns)
            n = b & 127
            if n >= 126: n = struct.unpack('!H' if n == 126 else '!Q', self.s.read(2 if n == 126 else 8, deadline_ns)[0])[0]
            payload, done = self.s.read(n, deadline_ns) if n else (b'', time.perf_counter_ns())
            op = a & 15
            if op == 9: self.send(payload, 10); continue
            if op == 8: raise EOFError('peer closed')
            if op == 1 and a & 128: return payload.decode(), self.s.first, done
            if self.any_frame: return payload, self.s.first, done
            raise ValueError('unexpected frame')

    any_frame = False


def load_stream(port, path, stop, counts):
    """Concurrent public-size stream through the same SSH connection (frames discarded)."""
    try:
        c = Client(Stream(tcp=port), path); c.any_frame = True
        while not stop.is_set():
            payload, _, _ = c.receive(time.perf_counter_ns() + 2_000_000_000)
            counts['frames'] += 1; counts['bytes'] += len(payload)
        c.s.close()
    except Exception as error:
        counts['error'] = type(error).__name__


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--tcp', type=int); g.add_argument('--ssh-stdio', type=int)
    p.add_argument('--session'); p.add_argument('--path', default=None)
    p.add_argument('--count', type=int, default=400); p.add_argument('--label', default='')
    p.add_argument('--load-port', type=int); p.add_argument('--load-path', default='/state')
    a = p.parse_args()
    assert not a.out.exists() and 1 <= a.count <= 5000
    path = a.path or ('/commands' if a.session else '/echo')
    stop, load = threading.Event(), dict(frames=0, bytes=0)
    if a.load_port:
        threading.Thread(target=load_stream, args=(a.load_port, a.load_path, stop, load), daemon=True).start()
        time.sleep(1)
    rows, conn, connections, started = [], None, 0, time.perf_counter_ns()
    wall = time.time_ns()
    for i in range(a.count):
        if conn is None:
            conn = Client(Stream(tcp=a.tcp, stdio=a.ssh_stdio), path); connections += 1
        rid = uuid.uuid4().hex
        msg = (dict(version=1, kind='private_health_probe', control_session_id=a.session, request_id=rid)
               if a.session else dict(kind='diagnostic_echo', request_id=rid))
        sent = time.perf_counter_ns(); deadline = sent + 200_000_000
        row = dict(index=i, request_id=rid, connection=connections, send_ns=sent)
        try:
            conn.send(json.dumps(msg, separators=(',', ':')))
            row['send_done_ns'] = time.perf_counter_ns()
            raw, first, done = conn.receive(deadline)
            reply = json.loads(raw)
            assert reply['request_id'] == rid
            if a.session:
                assert reply['accepted'] is True and reply['control_session_id'] == a.session
                h = reply['health']
                row.update(health_sample_host_mono_ms=h['health_sample_host_mono_ms'],
                           publisher_age_ms=h['publisher_age_ms'], neutral_age_ms=h['neutral_verification_age_ms'])
            row.update(first_byte_ns=first, complete_ns=done, rtt_ms=(done - sent) / 1e6, within_200ms=done - sent <= 200_000_000)
        except Exception as error:
            row.update(error=type(error).__name__, failure_ns=time.perf_counter_ns(), within_200ms=False)
            conn.s.close(); conn = None
        rows.append(row)
        time.sleep(max(0.001, 0.075 - (time.perf_counter_ns() - sent) / 1e9))
    if conn: conn.s.close()
    stop.set()
    ok = sorted(r['rtt_ms'] for r in rows if 'rtt_ms' in r)
    q = lambda f: round(ok[min(len(ok) - 1, int(len(ok) * f))], 3) if ok else None
    summary = dict(label=a.label, transport=f'tcp:{a.tcp}' if a.tcp else f'ssh-W:{a.ssh_stdio}', requests=len(rows), load=load,
                   failures=sum(not r['within_200ms'] for r in rows), connections=connections,
                   median_ms=q(.5), p90_ms=q(.9), p99_ms=q(.99), max_ms=q(1), over_130ms=sum(x > 130 for x in ok))
    report = dict(scope='Diagnostic probe-path client; not Unity, not qualification', participant=False, qualification=False,
                  client_clock='Windows Python perf_counter_ns', start_wall_utc_ns=wall, start_perf_ns=started,
                  summary=summary, rows=rows)
    with a.out.open('x') as f:
        json.dump(report, f); f.flush(); os.fsync(f.fileno())
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
