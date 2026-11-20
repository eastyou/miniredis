"""命令行客户端：连接 miniredis-server 并执行一条命令。"""

from __future__ import annotations

import argparse
import socket
import sys
from typing import Any, List, Optional, Sequence, Tuple

from .protocol import ProtocolError, encode_command, read_reply


def format_reply(kind: str, value: Any) -> Tuple[str, bool]:
    """把回复转换成可读文本，返回 ``(文本, 是否为错误)``。"""
    if kind == "error":
        return "(error) %s" % value, True
    if kind == "simple":
        return str(value), False
    if kind == "integer":
        return "(integer) %d" % value, False
    if kind == "bulk":
        return "(nil)" if value is None else str(value), False
    if kind == "array":
        if value is None:
            return "(nil array)", False
        if not value:
            return "(empty array)", False
        lines: List[str] = []
        for index, item in enumerate(value, start=1):
            item_kind, item_value = item
            text, _ = format_reply(item_kind, item_value)
            lines.append("%d) %s" % (index, text))
        return "\n".join(lines), False
    return str(value), False


def run_command(host: str, port: int, args: Sequence[str], timeout: float) -> int:
    payload = encode_command(list(args))
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(payload)
            stream = sock.makefile("rb")
            try:
                kind, value = read_reply(stream)
            finally:
                stream.close()
    except socket.timeout:
        print("连接超时: %s:%d" % (host, port), file=sys.stderr)
        return 2
    except OSError as exc:
        print("连接失败: %s" % exc, file=sys.stderr)
        return 2
    except ProtocolError as exc:
        print("协议错误: %s" % exc, file=sys.stderr)
        return 2

    text, is_error = format_reply(kind, value)
    stream_out = sys.stderr if is_error else sys.stdout
    print(text, file=stream_out)
    return 1 if is_error else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="miniredis-cli",
        description="miniredis 命令行客户端",
    )
    parser.add_argument("-H", "--host", default="127.0.0.1", help="服务端地址")
    parser.add_argument("-p", "--port", type=int, default=6399, help="服务端端口")
    parser.add_argument("--timeout", type=float, default=5.0, help="连接超时时间（秒）")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="要执行的命令")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help(sys.stderr)
        return 2
    return run_command(args.host, args.port, args.command, args.timeout)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
