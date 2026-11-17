#!/usr/bin/env python3
"""OrbStack 部署用的 TCP 转发。

ov.conf 里 openviking-server 只监听 127.0.0.1:1934，容器对外要提供 1933，
原先靠镜像里的 socat 转发，但本机源码构建的镜像并不含 socat（旧容器里的
是手工装的），这里用标准库实现同样的转发，MCP 的流式连接同样适用。

用法：openviking-orbstack-forward <listen_port> <upstream_host> <upstream_port>
"""

from __future__ import annotations

import asyncio
import sys

BUFFER = 64 * 1024


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(BUFFER)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            upstream_host, upstream_port
        )
    except OSError as exc:
        print(f"[ov-forward] upstream connect failed: {exc}", file=sys.stderr)
        client_writer.close()
        return
    try:
        await asyncio.gather(
            _pump(client_reader, upstream_writer),
            _pump(upstream_reader, client_writer),
        )
    finally:
        for writer in (upstream_writer, client_writer):
            try:
                writer.close()
            except OSError:
                pass


async def main(listen_port: int, upstream_host: str, upstream_port: int) -> None:
    server = await asyncio.start_server(
        lambda reader, writer: _handle(reader, writer, upstream_host, upstream_port),
        "0.0.0.0",
        listen_port,
    )
    print(
        f"[ov-forward] listening on 0.0.0.0:{listen_port} -> "
        f"{upstream_host}:{upstream_port}",
        flush=True,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    listen = int(sys.argv[1]) if len(sys.argv) > 1 else 1933
    up_host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    up_port = int(sys.argv[3]) if len(sys.argv) > 3 else 1934
    try:
        asyncio.run(main(listen, up_host, up_port))
    except KeyboardInterrupt:
        pass
