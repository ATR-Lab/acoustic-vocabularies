"""Byte relay for WebSocket traffic across the isolated container's Unix socket.

No DDS/UDP forwarding. Defaults/examples bind TCP to loopback; opt-in listener
addresses belong in private local configuration. Does not change firewall/Wi-Fi.
"""
import argparse
import asyncio
from pathlib import Path


async def main(args):
    async def relay(reader, writer):
        remote_writer = None
        try:
            if args.upstream_unix:
                remote_reader, remote_writer = await asyncio.open_unix_connection(args.upstream_unix)
            else:
                remote_reader, remote_writer = await asyncio.open_connection(args.upstream_host, args.upstream_port)
            async def copy(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()
            tasks = [asyncio.create_task(copy(reader, remote_writer)), asyncio.create_task(copy(remote_reader, writer))]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, ConnectionError):
            pass  # disconnected clients retry; no station endpoint is printed
        finally:
            for stream in (writer, remote_writer):
                if stream:
                    stream.close()
                    try:
                        await stream.wait_closed()
                    except (OSError, ConnectionError):
                        pass
    if args.listen_unix:
        if Path(args.listen_unix).exists():
            raise FileExistsError("Choose an unused socket path; do not overwrite another process")
        server = await asyncio.start_unix_server(relay, path=args.listen_unix)
    else:
        server = await asyncio.start_server(relay, args.listen_host, args.listen_port)
    print("Relay ready; forwards TCP byte stream only", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    listen = parser.add_mutually_exclusive_group(required=True)
    listen.add_argument("--listen-unix")
    listen.add_argument("--listen-port", type=int)
    parser.add_argument("--listen-host", default="127.0.0.1")
    upstream = parser.add_mutually_exclusive_group(required=True)
    upstream.add_argument("--upstream-unix")
    upstream.add_argument("--upstream-port", type=int)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    asyncio.run(main(parser.parse_args()))
