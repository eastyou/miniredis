from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from typing import Optional

from miniredis.server import MiniRedisServer


def encode(*args: str) -> bytes:
    """按 RESP 数组格式编码一条命令，测试里少写样板。"""
    parts = [b"*" + str(len(args)).encode() + b"\r\n"]
    for arg in args:
        data = arg.encode()
        parts.append(b"$" + str(len(data)).encode() + b"\r\n" + data + b"\r\n")
    return b"".join(parts)


class ServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = MiniRedisServer(port=0)
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def roundtrip(
        self, payload: bytes, length: int, server: Optional[MiniRedisServer] = None
    ) -> bytes:
        target = server if server is not None else self.server
        reader, writer = await asyncio.open_connection("127.0.0.1", target.port)
        try:
            writer.write(payload)
            await writer.drain()
            return await asyncio.wait_for(reader.readexactly(length), timeout=5)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass

    async def test_ping(self) -> None:
        self.assertEqual(await self.roundtrip(encode("PING"), 7), b"+PONG\r\n")

    async def test_inline_command(self) -> None:
        self.assertEqual(await self.roundtrip(b"PING\r\n", 7), b"+PONG\r\n")

    async def test_pipelining(self) -> None:
        payload = encode("PING") + encode("SET", "k", "hello") + encode("GET", "k")
        expected = b"+PONG\r\n+OK\r\n$5\r\nhello\r\n"
        self.assertEqual(await self.roundtrip(payload, len(expected)), expected)

    async def test_fragmented_request(self) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        try:
            writer.write(b"*2\r\n$4\r\nEC")
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.write(b"HO\r\n$2\r\nhi\r\n")
            await writer.drain()
            expected = b"$2\r\nhi\r\n"
            data = await asyncio.wait_for(
                reader.readexactly(len(expected)), timeout=5
            )
            self.assertEqual(data, expected)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_wrong_type_error(self) -> None:
        await self.roundtrip(encode("LPUSH", "l", "a"), 3)
        expected = (
            b"-WRONGTYPE Operation against a key holding the wrong kind of value\r\n"
        )
        self.assertEqual(
            await self.roundtrip(encode("GET", "l"), len(expected)), expected
        )

    async def test_unknown_command(self) -> None:
        expected = b"-ERR unknown command 'NOPE'\r\n"
        self.assertEqual(
            await self.roundtrip(encode("NOPE"), len(expected)), expected
        )

    async def test_quit_closes_connection(self) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        try:
            writer.write(encode("QUIT"))
            await writer.drain()
            self.assertEqual(
                await asyncio.wait_for(reader.readexactly(5), timeout=5), b"+OK\r\n"
            )
            self.assertEqual(await asyncio.wait_for(reader.read(), timeout=5), b"")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_protocol_error_closes_connection(self) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        try:
            writer.write(b"*1\r\n$-1\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), timeout=5)
            self.assertTrue(data.startswith(b"-ERR Protocol error:"))
            self.assertTrue(data.endswith(b"\r\n"))
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_two_clients_share_store(self) -> None:
        await self.roundtrip(encode("SET", "shared", "yes"), 5)
        expected = b"$3\r\nyes\r\n"
        self.assertEqual(
            await self.roundtrip(encode("GET", "shared"), len(expected)), expected
        )

    async def test_aof_persists_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.aof")
            first = MiniRedisServer(port=0, aof_path=path)
            await first.start()
            try:
                self.assertEqual(
                    await self.roundtrip(
                        encode("SET", "k", "v"), 5, server=first
                    ),
                    b"+OK\r\n",
                )
            finally:
                await first.stop()

            second = MiniRedisServer(port=0, aof_path=path)
            await second.start()
            try:
                self.assertEqual(
                    await self.roundtrip(
                        encode("GET", "k"), 7, server=second
                    ),
                    b"$1\r\nv\r\n",
                )
            finally:
                await second.stop()

    async def test_port_assigned(self) -> None:
        self.assertGreater(self.server.port, 0)


if __name__ == "__main__":
    unittest.main()
