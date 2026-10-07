"""Per-station Unix-socket gateway: station-UID ownership plus peer credentials.

Layout inside the per-station runtime directory (host
``/run/acoustic-vocab/<station_id>``, bind-mounted at ``/run/station``):

    state.sock              external public state gateway   (0600, station UID)
    commands.sock           external private command gateway (0600, station UID)
    internal/               0700, station UID
      state.sock            WebSocketTransport (public publisher)
      commands.sock         PrivateCommandTransport (already peer-checked)

Each accepted external connection is admitted only when the Linux
``SO_PEERCRED`` UID equals the provisioned station UID, then bytes are relayed
unmodified to the internal endpoint. Root and every other account are refused
and the refusal is journaled. Mode 0600 already stops other non-root users; the
peer check also refuses root and a mis-owned socket. Separate station accounts
are created and owned by the operator; this module never creates accounts,
changes ownership of another UID's files or touches host networking.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import socket
import stat
import struct
import threading

EXTERNAL = {'state': 'state.sock', 'commands': 'commands.sock'}
INTERNAL_DIRECTORY = 'internal'


def runtime_layout(runtime_dir):
    root = Path(runtime_dir)
    if not root.is_absolute():
        raise ValueError('Absolute per-station runtime directory required')
    internal = root/INTERNAL_DIRECTORY
    return dict(directory=root, internal=internal,
                external={name: root/file for name, file in EXTERNAL.items()},
                internal_sockets={name: internal/file for name, file in EXTERNAL.items()})


def directory_problem(info, uid, *, is_symlink=False):
    """Pure check of a stat result for a private station directory."""
    if is_symlink:
        return 'symlinked runtime directory'
    if not stat.S_ISDIR(info.st_mode):
        return 'runtime path is not a directory'
    if info.st_uid != uid:
        return 'runtime directory is not owned by the station UID'
    if info.st_mode & 0o077:
        return 'runtime directory must be mode 0700'
    return None


def socket_problem(info, uid):
    if not stat.S_ISSOCK(info.st_mode):
        return 'endpoint is not a Unix socket'
    if info.st_uid != uid:
        return 'endpoint is not owned by the station UID'
    if info.st_mode & 0o077:
        return 'endpoint must be mode 0600'
    return None


def admit_peer(peer_uid, allowed_uid):
    """Return None to admit, else a refusal reason."""
    if peer_uid is None:
        return 'PEER_CREDENTIALS_UNAVAILABLE'
    if type(peer_uid) is not int or peer_uid != allowed_uid:
        return 'UNKNOWN_PEER_UID'
    return None


def peer_uid(connection):
    """Linux SO_PEERCRED UID; fails closed elsewhere."""
    if not hasattr(socket, 'SO_PEERCRED'):
        raise OSError('SO_PEERCRED peer credentials unavailable on this platform')
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
    _, uid, _ = struct.unpack('3i', raw)
    return uid


def _check_parents(path):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Symlink in station runtime path')


def prepare_runtime(runtime_dir, uid, *, geteuid=None):
    """Validate the operator-created runtime directory and create ``internal/``.

    Refuses any pre-existing endpoint: stale sockets are removed by the reviewed
    service unit's ExecStopPost, never silently replaced here.
    """
    geteuid = geteuid or getattr(os, 'geteuid', None)
    if geteuid is None:
        raise OSError('Per-station Unix gateway requires Linux')
    if geteuid() != uid:
        raise ValueError('Gateway must run as the provisioned station UID')
    layout = runtime_layout(runtime_dir)
    root = layout['directory']
    _check_parents(root)
    problem = directory_problem(os.lstat(root), uid, is_symlink=root.is_symlink())
    if problem:
        raise ValueError(problem)
    if layout['internal'].exists():
        problem = directory_problem(os.lstat(layout['internal']), uid, is_symlink=layout['internal'].is_symlink())
        if problem:
            raise ValueError('internal '+problem)
    else:
        os.mkdir(layout['internal'], 0o700)
        os.chmod(layout['internal'], 0o700)
    for path in (*layout['external'].values(), *layout['internal_sockets'].values()):
        if path.exists() or path.is_symlink():
            raise FileExistsError('Inspect existing station endpoint before starting: '+path.name)
    return layout


class StationGateway:
    """Peer-checked relay from external station sockets to internal endpoints."""

    def __init__(self, layout, allowed_uid, event_sink, *, max_connections=16, credentials=peer_uid,
                 connect_timeout_s=2.0):
        if type(allowed_uid) is not int or allowed_uid < 1:
            raise ValueError('Provisioned non-root station UID required')
        if not callable(event_sink) or type(max_connections) is not int or not 1 <= max_connections <= 64:
            raise ValueError('Durable event sink and bounded connection limit required')
        self.layout, self.allowed_uid, self.event_sink = layout, allowed_uid, event_sink
        self.credentials, self.max_connections, self.connect_timeout_s = credentials, max_connections, connect_timeout_s
        self.targets = {}
        for name, path in layout['internal_sockets'].items():
            info = os.lstat(path)
            problem = socket_problem(info, allowed_uid)
            if problem:
                raise ValueError('internal '+name+': '+problem)
            self.targets[name] = (path, (info.st_dev, info.st_ino))
        self.counts = {name: dict(admitted=0, refused=0, relay_errors=0) for name in EXTERNAL}
        self.identities, self.servers, self.tasks = {}, [], set()
        self.loop, self.ready, self.failed = asyncio.new_event_loop(), threading.Event(), None
        self.thread = threading.Thread(target=self._run, daemon=True, name='station-gateway')
        self.thread.start()
        if not self.ready.wait(10) or self.failed:
            self.close()
            raise RuntimeError('Station gateway did not start') from self.failed

    def _bind(self, path):
        _check_parents(path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Default umask leaves a new socket without group/other write; the
            # parent is 0700. Tighten to 0600 before accepting any connection.
            listener.bind(str(path))
            os.chmod(path, 0o600)
            info = os.lstat(path)
            problem = socket_problem(info, self.allowed_uid)
            if problem:
                raise ValueError(problem)
            listener.listen(self.max_connections)
            listener.setblocking(False)
        except Exception:
            listener.close()
            raise
        self.identities[path] = (info.st_dev, info.st_ino)
        return listener

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            for name, path in self.layout['external'].items():
                listener = self._bind(path)
                server = self.loop.run_until_complete(asyncio.start_unix_server(
                    lambda reader, writer, name=name: self._handle(name, reader, writer), sock=listener))
                self.servers.append(server)
            self.ready.set()
            self.loop.run_forever()
        except Exception as error:
            self.failed = error
            self.ready.set()
        finally:
            self.loop.close()

    async def _handle(self, name, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        upstream = None
        try:
            connection = writer.get_extra_info('socket')
            try:
                uid = self.credentials(connection)
            except OSError:
                uid = None
            reason = admit_peer(uid, self.allowed_uid)
            if reason is None and len(self.tasks) > self.max_connections:
                reason = 'CONNECTION_LIMIT'
            if reason is not None:
                self.counts[name]['refused'] += 1
                self.event_sink('gateway_refused', endpoint=name, peer_uid=uid, reason=reason)
                return
            path, identity = self.targets[name]
            info = os.lstat(path)
            if (info.st_dev, info.st_ino) != identity or socket_problem(info, self.allowed_uid):
                raise RuntimeError('Internal endpoint replaced')
            upstream_reader, upstream = await asyncio.wait_for(asyncio.open_unix_connection(str(path)),
                                                               self.connect_timeout_s)
            self.counts[name]['admitted'] += 1

            async def pump(source, target):
                while True:
                    data = await source.read(65536)
                    if not data:
                        break
                    target.write(data)
                    await target.drain()
                if target.can_write_eof():
                    target.write_eof()
            copies = [asyncio.create_task(pump(reader, upstream)), asyncio.create_task(pump(upstream_reader, writer))]
            try:
                done, _ = await asyncio.wait(copies, return_when=asyncio.FIRST_COMPLETED)
                for work in done:
                    work.result()
                await asyncio.wait(copies, timeout=2)
            finally:
                for work in copies:
                    work.cancel()
                await asyncio.gather(*copies, return_exceptions=True)
        except (Exception, asyncio.CancelledError):
            self.counts[name]['relay_errors'] += 1
        finally:
            for stream in (writer, upstream):
                if stream is not None:
                    stream.close()
                    try:
                        await asyncio.wait_for(stream.wait_closed(), 2)
                    except Exception:
                        pass
            self.tasks.discard(task)

    def close(self):
        if self.thread.is_alive() and self.loop.is_running():
            async def finish():
                for server in self.servers:
                    server.close()
                for task in tuple(self.tasks):
                    task.cancel()
                await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
                for server in self.servers:
                    await server.wait_closed()
            asyncio.run_coroutine_threadsafe(finish(), self.loop).result(timeout=10)
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError('Station gateway did not stop')
        for path, identity in self.identities.items():
            # Remove only the socket inode this gateway created.
            if path.is_socket():
                info = os.lstat(path)
                if (info.st_dev, info.st_ino) == identity:
                    path.unlink()
        return dict(self.counts)
