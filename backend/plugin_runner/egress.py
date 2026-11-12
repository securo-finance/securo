"""The egress proxy: the plugins' only way out.

An HTTPS `CONNECT` proxy. The runner's network has no route to the internet,
so a plugin reaches a host only through here, and only when its signed pass
(`egress_token`) names that host and port. The proxy then tunnels raw bytes:
TLS runs end to end between the plugin and the host, which is what mutual TLS
with a client certificate needs, and the proxy never sees the traffic.

The proxy sits on both networks, so it also refuses any host that resolves to
a private, loopback or link-local address, and connects to the address it
checked rather than resolving twice. Plain-HTTP proxying is not offered.

Every decision is logged on the instance (plugin, host, port, allowed or
not); nothing leaves it.

Run with `python -m plugin_runner.egress`.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
import socket
from collections.abc import Awaitable, Callable

from plugin_runner.egress_token import EgressPass, InvalidPass, verify

logger = logging.getLogger("plugin_runner.egress")

HEADER_LIMIT = 8192
HANDSHAKE_TIMEOUT = 10
CONNECT_TIMEOUT = 15
IDLE_TIMEOUT = 300

Resolver = Callable[[str, int], Awaitable[list[str]]]


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class EgressProxy:
    def __init__(self, secret: str, *, resolver: Resolver = resolve, allow_private: bool = False):
        if not secret:
            raise ValueError("PLUGINS_EGRESS_SECRET is not set")
        self.secret = secret
        self.resolver = resolver
        # Only tests turn this on, to tunnel to a server on localhost.
        self.allow_private = allow_private

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HANDSHAKE_TIMEOUT)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                return
            upstream = await self._open(head, writer)
            if upstream is None:
                return
            up_reader, up_writer = upstream
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(
                _pipe(reader, up_writer), _pipe(up_reader, writer), return_exceptions=True
            )
        finally:
            writer.close()

    async def _open(
        self, head: bytes, writer: asyncio.StreamWriter
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or parts[0] != "CONNECT":
            await _refuse(writer, 405, "Only CONNECT is supported")
            return None
        host, _, port_text = parts[1].rpartition(":")
        host = host.strip("[]").lower().rstrip(".")
        if not host or not port_text.isdigit():
            await _refuse(writer, 400, "Bad target")
            return None
        port = int(port_text)

        granted = self._pass(lines[1:])
        if granted is None:
            logger.warning("egress refused: no valid pass, %s:%s", host, port)
            await _refuse(writer, 407, "Proxy authentication required")
            return None
        if not granted.allows(host, port):
            logger.warning("egress refused: %s is not allowed %s:%s", granted.plugin, host, port)
            await _refuse(writer, 403, "Host not declared by the plugin")
            return None
        try:
            addresses = await asyncio.wait_for(self.resolver(host, port), CONNECT_TIMEOUT)
        except (OSError, TimeoutError):
            await _refuse(writer, 502, "Could not resolve host")
            return None
        # Every address, not just the first: a name that also points inside
        # the network is refused as a whole.
        if not addresses or not (self.allow_private or all(is_public(a) for a in addresses)):
            logger.warning("egress refused: %s resolves %s to a private address", granted.plugin, host)
            await _refuse(writer, 403, "Host resolves to a private address")
            return None
        try:
            upstream = await asyncio.wait_for(asyncio.open_connection(addresses[0], port), CONNECT_TIMEOUT)
        except (OSError, TimeoutError):
            await _refuse(writer, 502, "Could not connect")
            return None
        logger.info("egress allowed: %s -> %s:%s", granted.plugin, host, port)
        return upstream

    def _pass(self, header_lines: list[str]) -> EgressPass | None:
        for line in header_lines:
            name, _, value = line.partition(":")
            if name.strip().lower() != "proxy-authorization":
                continue
            scheme, _, credentials = value.strip().partition(" ")
            if scheme.lower() != "basic":
                return None
            try:
                _, _, token = base64.b64decode(credentials, validate=True).decode().partition(":")
                return verify(self.secret, token)
            except (ValueError, InvalidPass):
                return None
        return None


async def _refuse(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    extra = 'Proxy-Authenticate: Basic realm="securo-plugins"\r\n' if status == 407 else ""
    writer.write(f"HTTP/1.1 {status} {reason}\r\n{extra}Content-Length: 0\r\nConnection: close\r\n\r\n".encode())
    try:
        await writer.drain()
    except ConnectionError:
        pass


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def serve(host: str = "0.0.0.0", port: int = 3128) -> None:
    proxy = EgressProxy(os.environ.get("PLUGINS_EGRESS_SECRET", ""))
    server = await asyncio.start_server(proxy.handle, host, port, limit=HEADER_LIMIT)
    logger.info("egress proxy listening on %s:%s", host, port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(serve(port=int(os.environ.get("PLUGINS_EGRESS_PORT", "3128"))))
