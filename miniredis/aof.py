"""AOF（Append Only File）持久化。

每条写命令以 RESP 数组格式追加到文件，重启时按顺序重放。实现刻意保持简单：
不做 fsync、不做重写，适合演示与学习，不适合生产环境。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, List, Union

from .protocol import CommandParser, ProtocolError, encode_command

if TYPE_CHECKING:  # pragma: no cover
    from .commands import Executor


class Aof:
    """只追加的命令日志。"""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "ab", buffering=0)

    def append(self, args: List[str]) -> None:
        if not args:
            return
        self._handle.write(encode_command(args))

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> "Aof":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def replay(path: Union[str, Path], executor: "Executor") -> int:
    """重放 AOF 文件中的命令，返回成功执行的条数。

    文件不存在时返回 0；末尾被截断的不完整命令会被忽略，避免启动失败。
    """
    file_path = Path(path)
    if not file_path.exists():
        return 0
    with open(file_path, "rb") as handle:
        data = handle.read()
    parser = CommandParser()
    parser.feed(data)
    executed = 0
    while True:
        try:
            args = parser.parse()
        except ProtocolError:
            break
        if args is None:
            break
        if not args:
            continue
        executor.execute(args)
        executed += 1
    return executed


def remove(path: Union[str, Path]) -> None:
    """删除 AOF 文件，测试与重置时使用。"""
    try:
        os.remove(path)
    except FileNotFoundError:
        return
