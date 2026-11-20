"""命令分发与语义实现。

每条命令执行后直接返回已编码的 RESP 字节，服务端只需拼接写回，减少一次中间对象。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence

from .protocol import (
    encode_array,
    encode_bulk,
    encode_error,
    encode_integer,
    encode_simple,
)
from .store import Store, WrongType

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型检查
    from .aof import Aof

CommandHandler = Callable[["Executor", List[str]], bytes]


class CommandError(Exception):
    """命令层错误，消息会原样作为 RESP 错误回复。"""


MUTATING: frozenset = frozenset(
    {
        "set",
        "del",
        "incr",
        "decr",
        "expire",
        "persist",
        "lpush",
        "rpush",
        "hset",
        "hdel",
        "sadd",
        "srem",
        "flushdb",
    }
)


def _arity(args: Sequence[str], minimum: int, maximum: Optional[int] = None) -> None:
    """校验参数个数（不含命令名）。"""
    name = args[0].lower()
    given = len(args) - 1
    if given < minimum or (maximum is not None and given > maximum):
        raise CommandError("ERR wrong number of arguments for '%s' command" % name)


def _to_int(raw: str, label: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise CommandError("ERR %s is not an integer or out of range" % label) from None


class Executor:
    """把命令字符串分派到具体实现，并负责 AOF 追加。"""

    def __init__(self, store: Store, aof: Optional["Aof"] = None) -> None:
        self.store = store
        self.aof = aof

    def execute(self, args: Sequence[str]) -> bytes:
        if not args:
            return encode_error("ERR empty command")
        name = args[0].lower()
        handler = COMMANDS.get(name)
        if handler is None:
            return encode_error("ERR unknown command '%s'" % args[0])
        try:
            reply = handler(self, list(args))
        except CommandError as exc:
            return encode_error(str(exc))
        except WrongType:
            return encode_error(
                "WRONGTYPE Operation against a key holding the wrong kind of value"
            )
        if name in MUTATING and self.aof is not None:
            self.aof.append(list(args))
        return reply


# ---- 连接 ----

def cmd_ping(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 0, 1)
    if len(args) == 2:
        return encode_bulk(args[1])
    return encode_simple("PONG")


def cmd_echo(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_bulk(args[1])


# ---- string ----

def cmd_set(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 2)
    key = args[1]
    value = args[2]
    expire_at: Optional[float] = None
    index = 3
    while index < len(args):
        option = args[index].lower()
        if option == "ex":
            if index + 1 >= len(args):
                raise CommandError("ERR syntax error")
            seconds = _to_int(args[index + 1], "expire time")
            if seconds <= 0:
                raise CommandError("ERR invalid expire time in 'set' command")
            expire_at = executor.store.now() + seconds
            index += 2
        elif option == "px":
            if index + 1 >= len(args):
                raise CommandError("ERR syntax error")
            milliseconds = _to_int(args[index + 1], "expire time")
            if milliseconds <= 0:
                raise CommandError("ERR invalid expire time in 'set' command")
            expire_at = executor.store.now() + milliseconds / 1000.0
            index += 2
        else:
            raise CommandError("ERR syntax error")
    executor.store.set_string(key, value, expire_at)
    return encode_simple("OK")


def cmd_get(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_bulk(executor.store.get_string(args[1]))


def cmd_exists(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1)
    count = sum(1 for key in args[1:] if executor.store.exists(key))
    return encode_integer(count)


def cmd_del(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1)
    return encode_integer(executor.store.delete(args[1:]))


def cmd_incr(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return _change_integer(executor, args[1], 1)


def cmd_decr(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return _change_integer(executor, args[1], -1)


def _change_integer(executor: Executor, key: str, delta: int) -> bytes:
    try:
        value = executor.store.incr_by(key, delta)
    except ValueError:
        return encode_error("ERR value is not an integer or out of range")
    return encode_integer(value)


def cmd_expire(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 2, 2)
    seconds = _to_int(args[2], "expire time")
    return encode_integer(1 if executor.store.set_expire(args[1], seconds) else 0)


def cmd_ttl(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_integer(executor.store.ttl(args[1]))


def cmd_persist(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_integer(1 if executor.store.persist(args[1]) else 0)


def cmd_keys(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_array(executor.store.keys(args[1]))


def cmd_type(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_simple(executor.store.type_name(args[1]))


# ---- list ----

def cmd_lpush(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 2)
    return encode_integer(executor.store.list_push(args[1], args[2:], left=True))


def cmd_rpush(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 2)
    return encode_integer(executor.store.list_push(args[1], args[2:], left=False))


def cmd_lrange(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 3, 3)
    start = _to_int(args[2], "start index")
    stop = _to_int(args[3], "stop index")
    return encode_array(executor.store.list_range(args[1], start, stop))


def cmd_llen(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_integer(executor.store.list_len(args[1]))


# ---- hash ----

def cmd_hset(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 3)
    if (len(args) - 2) % 2 != 0:
        raise CommandError("ERR wrong number of arguments for 'hset' command")
    pairs = [(args[i], args[i + 1]) for i in range(2, len(args), 2)]
    return encode_integer(executor.store.hash_set(args[1], pairs))


def cmd_hget(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 2, 2)
    return encode_bulk(executor.store.hash_get(args[1], args[2]))


def cmd_hdel(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 2)
    return encode_integer(executor.store.hash_del(args[1], args[2:]))


def cmd_hlen(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_integer(executor.store.hash_len(args[1]))


# ---- set ----

def cmd_sadd(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 2)
    return encode_integer(executor.store.set_add(args[1], args[2:]))


def cmd_srem(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 2)
    return encode_integer(executor.store.set_remove(args[1], args[2:]))


def cmd_smembers(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_array(executor.store.set_members(args[1]))


def cmd_scard(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 1, 1)
    return encode_integer(executor.store.set_len(args[1]))


# ---- 管理 ----

def cmd_dbsize(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 0, 0)
    return encode_integer(executor.store.dbsize())


def cmd_flushdb(executor: Executor, args: List[str]) -> bytes:
    _arity(args, 0, 0)
    executor.store.flush()
    return encode_simple("OK")


COMMANDS: Dict[str, CommandHandler] = {
    "ping": cmd_ping,
    "echo": cmd_echo,
    "set": cmd_set,
    "get": cmd_get,
    "exists": cmd_exists,
    "del": cmd_del,
    "incr": cmd_incr,
    "decr": cmd_decr,
    "expire": cmd_expire,
    "ttl": cmd_ttl,
    "persist": cmd_persist,
    "keys": cmd_keys,
    "type": cmd_type,
    "lpush": cmd_lpush,
    "rpush": cmd_rpush,
    "lrange": cmd_lrange,
    "llen": cmd_llen,
    "hset": cmd_hset,
    "hget": cmd_hget,
    "hdel": cmd_hdel,
    "hlen": cmd_hlen,
    "sadd": cmd_sadd,
    "srem": cmd_srem,
    "smembers": cmd_smembers,
    "scard": cmd_scard,
    "dbsize": cmd_dbsize,
    "flushdb": cmd_flushdb,
}
