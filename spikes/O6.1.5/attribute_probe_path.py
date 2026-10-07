"""Split each diagnostic probe round trip into relay-host and outside-relay stages.

Inputs: a probe_path_client.py report (Windows clock), the relay trace for the
private relay (host clock) and that client's relay connection index, and
optionally the service private-timing trace (same host monotonic clock).
No clock mapping is assumed: every stage is a difference within one clock.

  relay_residence = relay reply write drained - relay request read returned
  outside_relay   = client RTT - relay_residence   (Windows client, Windows
                    OpenSSH, network, Linux sshd and loopback TCP to the relay)
  service_handler = service send_end - first socket_data callback (if traced)
"""
import argparse, json, statistics
from pathlib import Path


def q(values, f):
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * f))], 3) if s else None


def stats(values):
    return dict(n=len(values), median=q(values, .5), p90=q(values, .9), p99=q(values, .99), max=q(values, 1)) if values else dict(n=0)


def pair(client, relay, connection, min_request_bytes=40):
    rows = [r for r in relay['rows'] if r[1] == connection]
    requests = [r for r in rows if r[2] == 0 and r[3] == 0 and r[4] >= min_request_bytes]
    replies = [r for r in rows if r[2] == 1 and r[3] == 1]
    out = []
    ok = [r for r in client['rows'] if 'rtt_ms' in r or 'error' in r]
    # Skip the upgrade request (first client->service read) and its 101 reply.
    requests, replies = requests[1:], replies[1:]
    for c, req in zip(ok, requests):
        rep = next((r for r in replies if r[0] >= req[0]), None)
        if rep is None:
            out.append(dict(index=c['index'], request_id=c['request_id'], rtt_ms=c.get('rtt_ms'), error=c.get('error'))); continue
        replies.remove(rep)
        residence = (rep[0] - req[0]) / 1e6
        row = dict(index=c['index'], request_id=c['request_id'], rtt_ms=c.get('rtt_ms'), error=c.get('error'),
                   relay_request_host_ns=req[0], relay_reply_drained_host_ns=rep[0], relay_residence_ms=residence)
        if c.get('rtt_ms') is not None: row['outside_relay_ms'] = c['rtt_ms'] - residence
        out.append(row)
    return out


def service_spans(trace):
    by = {}
    fields = trace['fields']; i = {k: n for n, k in enumerate(fields)}
    last_data = {}
    for r in sorted(trace['rows'], key=lambda r: r[i['host_ns']]):
        kind = r[i['kind']]
        if kind == 'socket_data': last_data.setdefault(r[i['connection']], []).append(r[i['host_ns']])
        elif kind in ('probe_ingress', 'send_begin', 'send_end') and r[i['request_id']]:
            by.setdefault(r[i['request_id']], {})[kind] = r[i['host_ns']]
    return by


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--client', type=Path, required=True); p.add_argument('--relay', type=Path, required=True)
    p.add_argument('--connection', type=int, required=True); p.add_argument('--service', type=Path)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    client = json.loads(a.client.read_text()); relay = json.loads(a.relay.read_text())
    rows = pair(client, relay, a.connection)
    if a.service:
        spans = service_spans(json.loads(a.service.read_text()))
        for row in rows:
            s = spans.get(row['request_id'])
            if s and 'relay_request_host_ns' in row and 'probe_ingress' in s and 'send_end' in s:
                row['relay_to_service_ingress_ms'] = (s['probe_ingress'] - row['relay_request_host_ns']) / 1e6
                row['service_ingress_to_send_end_ms'] = (s['send_end'] - s['probe_ingress']) / 1e6
                row['service_send_end_to_relay_drained_ms'] = (row['relay_reply_drained_host_ns'] - s['send_end']) / 1e6
    keys = ['rtt_ms', 'relay_residence_ms', 'outside_relay_ms', 'relay_to_service_ingress_ms',
            'service_ingress_to_send_end_ms', 'service_send_end_to_relay_drained_ms']
    summary = dict(label=client['summary'].get('label'), transport=client['summary'].get('transport'),
                   requests=len(client['rows']), failures=client['summary']['failures'], paired=sum('relay_residence_ms' in r for r in rows),
                   relay_trace_complete=relay.get('complete'),
                   **{k: stats([r[k] for r in rows if r.get(k) is not None]) for k in keys})
    if a.out:
        with a.out.open('x') as f: json.dump(dict(summary=summary, rows=rows), f)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
