"""RESP2 协议的编解码实现。

请求统一解析为 ``list[str]``，响应统一编码为 ``bytes``，方便服务端直接写回
socket。解析器是增量的：网络数据分片到达时先调用 :meth:`CommandParser.feed`，
再循环调用 :meth:`CommandParser.parse`，返回 ``None`` 表示数据还不完整。
"""

from __future__ import annotations

from typing import Any, BinaryIO, List, Optional, Sequence, Tuple, Union

CRLF = b"\r\n"
MAX_BULK = 512 * 1024 * 1024
MAX_ARRAY = 1024 * 1024
MAX_INLINE = 64 * 1024

BulkValue = Optional[Union[str, bytes]]


class ProtocolError(Exception):
    """协议数据非法，服务端应回复错误并关闭连接。"""


def encode_simple(text: str) -> bytes:
    """简单字符串，形如 ``+OK``。"""
    return b"+" + text.encode("utf-8", "surrogateescape") + CRLF


def encode_error(text: str) -> bytes:
    """错误回复，形如 ``-ERR unknown command``。"""
    return b"-" + text.encode("utf-8", "surrogateescape") + CRLF


def encode_integer(value: int) -> bytes:
    """整数回复，形如 ``:1``。"""
    return b":" + str(int(value)).encode("ascii") + CRLF


def encode_bulk(value: BulkValue) -> bytes:
    """批量字符串，``None`` 编码为 RESP 的空值 ``$-1``。"""
    if value is None:
        return b"$-1\r\n"
    data = value.encode("utf-8", "surrogateescape") if isinstance(value, str) else value
    return b"$" + str(len(data)).encode("ascii") + CRLF + data + CRLF


def encode_array(values: Optional[Sequence[BulkValue]]) -> bytes:
    """批量字符串数组，``None`` 编码为 RESP 的空数组 ``*-1``。"""
    if values is None:
        return b"*-1\r\n"
    parts = [b"*" + str(len(values)).encode("ascii") + CRLF]
    for value in values:
        parts.append(encode_bulk(value))
    return b"".join(parts)


def encode_command(args: Sequence[str]) -> bytes:
    """把一条命令编码成客户端请求格式，AOF 落盘也用它。"""
    return encode_array(list(args))


class CommandParser:
    """增量 RESP 请求解析器。

    同时支持 RESP 数组请求和 telnet 风格的行内命令，和 Redis 保持一致。
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("feed() 只接受字节数据")
        self._buffer.extend(data)

    def pending(self) -> int:
        """返回尚未消费的字节数，主要用于测试与监控。"""
        return len(self._buffer)

    def parse(self) -> Optional[List[str]]:
        """解析一条命令。

        返回 ``None`` 表示数据不完整，需要继续读取；返回 ``[]`` 表示收到空行，
        调用方可以忽略。协议非法时抛出 :class:`ProtocolError`。
        """
        if not self._buffer:
            return None
        if self._buffer[0:1] == b"*":
            return self._parse_multibulk()
        return self._parse_inline()

    def _parse_multibulk(self) -> Optional[List[str]]:
        buffer = self._buffer
        line_end = buffer.find(CRLF)
        if line_end < 0:
            if len(buffer) > MAX_INLINE:
                raise ProtocolError("too big inline request")
            return None
        try:
            count = int(bytes(buffer[1:line_end]))
        except ValueError:
            raise ProtocolError("invalid multibulk length") from None
        if count <= 0:
            del buffer[: line_end + 2]
            return []
        if count > MAX_ARRAY:
            raise ProtocolError("invalid multibulk length")

        position = line_end + 2
        args: List[str] = []
        for _ in range(count):
            header_end = buffer.find(CRLF, position)
            if header_end < 0:
                return None
            if buffer[position : position + 1] != b"$":
                raise ProtocolError("expected '$', got something else")
            try:
                length = int(bytes(buffer[position + 1 : header_end]))
            except ValueError:
                raise ProtocolError("invalid bulk length") from None
            if length < 0 or length > MAX_BULK:
                raise ProtocolError("invalid bulk length")
            body_start = header_end + 2
            body_end = body_start + length
            if len(buffer) < body_end + 2:
                return None
            if bytes(buffer[body_end : body_end + 2]) != CRLF:
                raise ProtocolError("missing CRLF after bulk string")
            args.append(bytes(buffer[body_start:body_end]).decode("utf-8", "surrogateescape"))
            position = body_end + 2

        del buffer[:position]
        return args

    def _parse_inline(self) -> Optional[List[str]]:
        buffer = self._buffer
        line_end = buffer.find(b"\n")
        if line_end < 0:
            if len(buffer) > MAX_INLINE:
                raise ProtocolError("too big inline request")
            return None
        line = bytes(buffer[:line_end])
        del buffer[: line_end + 1]
        if line.endswith(b"\r"):
            line = line[:-1]
        text = line.decode("utf-8", "surrogateescape").strip()
        if not text:
            return []
        return text.split()


def read_reply(stream: BinaryIO) -> Tuple[str, Any]:
    """从阻塞式二进制流（如 ``socket.makefile('rb')``）读取一个完整回复。

    返回 ``(类型, 值)``，类型取 ``simple`` / ``error`` / ``integer`` / ``bulk`` /
    ``array``，供命令行客户端渲染。数据不完整或非法时抛出 :class:`ProtocolError`。
    """
    line = stream.readline()
    if not line:
        raise ProtocolError("connection closed")
    if not line.endswith(b"\r\n"):
        raise ProtocolError("malformed reply line")
    kind = line[0:1]
    payload = line[1:-2]

    if kind == b"+":
        return "simple", payload.decode("utf-8", "surrogateescape")
    if kind == b"-":
        return "error", payload.decode("utf-8", "surrogateescape")
    if kind == b":":
        try:
            return "integer", int(payload)
        except ValueError:
            raise ProtocolError("invalid integer reply") from None
    if kind == b"$":
        try:
            length = int(payload)
        except ValueError:
            raise ProtocolError("invalid bulk length reply") from None
        if length < 0:
            return "bulk", None
        data = stream.read(length + 2)
        if len(data) != length + 2 or not data.endswith(b"\r\n"):
            raise ProtocolError("truncated bulk reply")
        return "bulk", data[:-2].decode("utf-8", "surrogateescape")
    if kind == b"*":
        try:
            count = int(payload)
        except ValueError:
            raise ProtocolError("invalid array length reply") from None
        if count < 0:
            return "array", None
        return "array", [read_reply(stream) for _ in range(count)]
    raise ProtocolError("unknown reply type")
