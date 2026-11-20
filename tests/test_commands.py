from __future__ import annotations

import unittest

from miniredis.commands import Executor
from miniredis.store import Store


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class CommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = Store(clock=self.clock)
        self.executor = Executor(self.store)

    def run_cmd(self, *args: str) -> bytes:
        return self.executor.execute(list(args))

    def test_ping(self) -> None:
        self.assertEqual(self.run_cmd("PING"), b"+PONG\r\n")
        self.assertEqual(self.run_cmd("ping", "hi"), b"$2\r\nhi\r\n")

    def test_echo(self) -> None:
        self.assertEqual(self.run_cmd("ECHO", "abc"), b"$3\r\nabc\r\n")
        self.assertEqual(
            self.run_cmd("ECHO"),
            b"-ERR wrong number of arguments for 'echo' command\r\n",
        )

    def test_unknown_command(self) -> None:
        self.assertEqual(
            self.run_cmd("HELLO"), b"-ERR unknown command 'HELLO'\r\n"
        )

    def test_set_get(self) -> None:
        self.assertEqual(self.run_cmd("SET", "k", "v"), b"+OK\r\n")
        self.assertEqual(self.run_cmd("GET", "k"), b"$1\r\nv\r\n")
        self.assertEqual(self.run_cmd("GET", "missing"), b"$-1\r\n")

    def test_set_with_ex(self) -> None:
        self.assertEqual(self.run_cmd("SET", "k", "v", "EX", "10"), b"+OK\r\n")
        self.assertEqual(self.run_cmd("TTL", "k"), b":10\r\n")
        self.clock.advance(11)
        self.assertEqual(self.run_cmd("GET", "k"), b"$-1\r\n")

    def test_set_with_px(self) -> None:
        self.assertEqual(self.run_cmd("SET", "k", "v", "PX", "500"), b"+OK\r\n")
        self.clock.advance(0.6)
        self.assertEqual(self.run_cmd("GET", "k"), b"$-1\r\n")

    def test_set_invalid_expire(self) -> None:
        self.assertEqual(
            self.run_cmd("SET", "k", "v", "EX", "0"),
            b"-ERR invalid expire time in 'set' command\r\n",
        )
        self.assertEqual(self.run_cmd("SET", "k", "v", "EX"), b"-ERR syntax error\r\n")

    def test_set_syntax_error(self) -> None:
        self.assertEqual(
            self.run_cmd("SET", "k", "v", "WHAT"), b"-ERR syntax error\r\n"
        )

    def test_expire_ttl_persist(self) -> None:
        self.run_cmd("SET", "k", "v")
        self.assertEqual(self.run_cmd("TTL", "k"), b":-1\r\n")
        self.assertEqual(self.run_cmd("EXPIRE", "k", "30"), b":1\r\n")
        self.assertEqual(self.run_cmd("TTL", "k"), b":30\r\n")
        self.assertEqual(self.run_cmd("PERSIST", "k"), b":1\r\n")
        self.assertEqual(self.run_cmd("TTL", "k"), b":-1\r\n")
        self.assertEqual(self.run_cmd("TTL", "nope"), b":-2\r\n")
        self.assertEqual(self.run_cmd("EXPIRE", "nope", "5"), b":0\r\n")

    def test_incr_decr(self) -> None:
        self.assertEqual(self.run_cmd("INCR", "n"), b":1\r\n")
        self.assertEqual(self.run_cmd("INCR", "n"), b":2\r\n")
        self.assertEqual(self.run_cmd("DECR", "n"), b":1\r\n")
        self.run_cmd("SET", "s", "abc")
        self.assertEqual(
            self.run_cmd("INCR", "s"),
            b"-ERR value is not an integer or out of range\r\n",
        )

    def test_exists_del(self) -> None:
        self.run_cmd("SET", "a", "1")
        self.run_cmd("SET", "b", "2")
        self.assertEqual(self.run_cmd("EXISTS", "a", "a", "b", "x"), b":3\r\n")
        self.assertEqual(self.run_cmd("DEL", "a", "x"), b":1\r\n")
        self.assertEqual(self.run_cmd("EXISTS", "a"), b":0\r\n")

    def test_keys(self) -> None:
        self.run_cmd("SET", "user:1", "x")
        self.run_cmd("SET", "user:2", "x")
        self.run_cmd("SET", "order:1", "x")
        self.assertEqual(
            self.run_cmd("KEYS", "user:*"),
            b"*2\r\n$6\r\nuser:1\r\n$6\r\nuser:2\r\n",
        )

    def test_wrong_type(self) -> None:
        self.run_cmd("LPUSH", "l", "a")
        self.assertEqual(
            self.run_cmd("GET", "l"),
            b"-WRONGTYPE Operation against a key holding the wrong kind of value\r\n",
        )

    def test_list(self) -> None:
        self.assertEqual(self.run_cmd("RPUSH", "l", "a", "b"), b":2\r\n")
        self.assertEqual(self.run_cmd("LPUSH", "l", "c"), b":3\r\n")
        self.assertEqual(
            self.run_cmd("LRANGE", "l", "0", "-1"),
            b"*3\r\n$1\r\nc\r\n$1\r\na\r\n$1\r\nb\r\n",
        )
        self.assertEqual(self.run_cmd("LLEN", "l"), b":3\r\n")
        self.assertEqual(self.run_cmd("LLEN", "none"), b":0\r\n")

    def test_list_bad_index(self) -> None:
        self.run_cmd("RPUSH", "l", "a")
        self.assertEqual(
            self.run_cmd("LRANGE", "l", "x", "1"),
            b"-ERR start index is not an integer or out of range\r\n",
        )

    def test_hash(self) -> None:
        self.assertEqual(self.run_cmd("HSET", "h", "a", "1", "b", "2"), b":2\r\n")
        self.assertEqual(self.run_cmd("HSET", "h", "a", "3"), b":0\r\n")
        self.assertEqual(self.run_cmd("HGET", "h", "a"), b"$1\r\n3\r\n")
        self.assertEqual(self.run_cmd("HGET", "h", "zz"), b"$-1\r\n")
        self.assertEqual(self.run_cmd("HLEN", "h"), b":2\r\n")
        self.assertEqual(self.run_cmd("HDEL", "h", "a"), b":1\r\n")

    def test_hset_odd_arguments(self) -> None:
        self.assertEqual(
            self.run_cmd("HSET", "h", "a"),
            b"-ERR wrong number of arguments for 'hset' command\r\n",
        )

    def test_set_commands(self) -> None:
        self.assertEqual(self.run_cmd("SADD", "s", "a", "b", "a"), b":2\r\n")
        self.assertEqual(self.run_cmd("SCARD", "s"), b":2\r\n")
        self.assertEqual(
            self.run_cmd("SMEMBERS", "s"), b"*2\r\n$1\r\na\r\n$1\r\nb\r\n"
        )
        self.assertEqual(self.run_cmd("SREM", "s", "a", "x"), b":1\r\n")

    def test_dbsize_and_flush(self) -> None:
        self.run_cmd("SET", "a", "1")
        self.run_cmd("SET", "b", "2")
        self.assertEqual(self.run_cmd("DBSIZE"), b":2\r\n")
        self.assertEqual(self.run_cmd("FLUSHDB"), b"+OK\r\n")
        self.assertEqual(self.run_cmd("DBSIZE"), b":0\r\n")

    def test_type(self) -> None:
        self.run_cmd("RPUSH", "l", "a")
        self.assertEqual(self.run_cmd("TYPE", "l"), b"+list\r\n")
        self.assertEqual(self.run_cmd("TYPE", "none"), b"+none\r\n")

    def test_empty_command(self) -> None:
        self.assertEqual(self.executor.execute([]), b"-ERR empty command\r\n")

    def test_arity_errors(self) -> None:
        self.assertEqual(
            self.run_cmd("GET"),
            b"-ERR wrong number of arguments for 'get' command\r\n",
        )
        self.assertEqual(
            self.run_cmd("PING", "a", "b"),
            b"-ERR wrong number of arguments for 'ping' command\r\n",
        )


if __name__ == "__main__":
    unittest.main()
