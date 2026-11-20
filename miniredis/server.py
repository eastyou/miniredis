"""asyncio TCP 服务端：解析 RESP 请求、执行命令、写回响应。"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import List, Optional, Sequence

from .aof import Aof, replay
from .commands import Executor
from .protocol import (
    CommandParser,
    ProtocolError,
    encode_error,
    encode_simple,
)
from .store import Store

DEFAULT_PORT = 6399


class MiniRedisServer:
    """迷你 Redis 服务端。

    ``port=0`` 时由系统分配空闲端口，绑定后可通过 :attr:`port` 读取真实端口，
    测试里常用这种方式避免端口冲突。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        aof_path: Optional[str] = None,
        store: Optional[Store] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.store = store if store is not None else Store()
        self.executor = Executor(self.store)
        self.aof: Optional[Aof] = None
        if aof_path is not None:
            replay(aof_path, self.executor)
            self.aof = Aof(aof_path)
            self.executor.aof = self.aof
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        sockets = self._server.sockets or []
        if sockets:
            self.port = int(sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.aof is not None:
            self.aof.close()

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        parser = CommandParser()
        quitting = False
        try:
            while not quitting:
                try:
                    chunk = await reader.read(65536)
                except (ConnectionResetError, asyncio.IncompleteReadError):
                    break
                if not chunk:
                    break
                parser.feed(chunk)
                replies: List[bytes] = []
                while True:
                    try:
                        args = parser.parse()
                    except ProtocolError as exc:
                        replies.append(encode_error("ERR Protocol error: %s" % exc))
                        quitting = True
                        break
                    if args is None:
                        break
                    if not args:
                        continue
                    if args[0].lower() == "quit":
                        replies.append(encode_simple("OK"))
                        quitting = True
                        break
                    replies.append(self.executor.execute(args))
                if replies:
                    writer.write(b"".join(replies))
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass


async def _run_server(host: str, port: int, aof_path: Optional[str]) -> None:
    server = MiniRedisServer(host=host, port=port, aof_path=aof_path)
    await server.start()
    print(
        "miniredis 已启动: redis://%s:%d（Ctrl+C 退出）" % (host, server.port),
        flush=True,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            # Windows 不支持 add_signal_handler，退化为 KeyboardInterrupt。
            pass

    try:
        await stop_event.wait()
    finally:
        await server.stop()
        print("miniredis 已停止", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="miniredis-server",
        description="用 asyncio 实现的迷你 Redis 服务端（RESP2 协议）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 6399"
    )
    parser.add_argument("--aof", default=None, help="AOF 文件路径，开启持久化")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(_run_server(args.host, args.port, args.aof))
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print("启动失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
