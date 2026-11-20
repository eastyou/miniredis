from __future__ import annotations

import io
import unittest

from miniredis.protocol import (
    CommandParser,
    ProtocolError,
    encode_array,
    encode_bulk,
    encode_command,
    encode_error,
    encode_integer,
    encode_simple,
    read_reply,
)


class EncodeTests(unittest.TestCase):
    def test_simple(self) -> None:
        self.assertEqual(encode_simple("OK"), b"+OK\r\n")

    def test_error(self) -> None:
        self.assertEqual(encode_error("ERR boom"), b"-ERR boom\r\n")

    def test_integer(self) -> None:
        self.assertEqual(encode_integer(-3), b":-3\r\n")

    def test_bulk(self) -> None:
        self.assertEqual(encode_bulk("hi"), b"$2\r\nhi\r\n")
        self.assertEqual(encode_bulk(b"\x00\xff"), b"$2\r\n\x00\xff\r\n")
        self.assertEqual(encode_bulk(None), b"$-1\r\n")

    def test_array(self) -> None:
        self.assertEqual(
            encode_array(["a", "bc"]), b"*2\r\n$1\r\na\r\n$2\r\nbc\r\n"
        )
        self.assertEqual(encode_array(None), b"*-1\r\n")
        self.assertEqual(encode_array([]), b"*0\r\n")

    def test_encode_command(self) -> None:
        self.assertEqual(
            encode_command(["SET", "k", "v"]),
            b"*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$1\r\nv\r\n",
        )


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = CommandParser()

    def test_multibulk_complete(self) -> None:
        self.parser.feed(b"*2\r\n$4\r\nECHO\r\n$5\r\nhello\r\n")
        self.assertEqual(self.parser.parse(), ["ECHO", "hello"])
        self.assertIsNone(self.parser.parse())

    def test_multibulk_fragmented(self) -> None:
        self.parser.feed(b"*2\r\n$4\r\nEC")
        self.assertIsNone(self.parser.parse())
        self.parser.feed(b"HO\r\n$5\r\nhel")
        self.assertIsNone(self.parser.parse())
        self.parser.feed(b"lo\r\n")
        self.assertEqual(self.parser.parse(), ["ECHO", "hello"])

    def test_two_commands_in_one_feed(self) -> None:
        self.parser.feed(b"*1\r\n$4\r\nPING\r\nPING\r\n")
        self.assertEqual(self.parser.parse(), ["PING"])
        self.assertEqual(self.parser.parse(), ["PING"])
        self.assertIsNone(self.parser.parse())

    def test_inline(self) -> None:
        self.parser.feed(b"PING\r\n")
        self.assertEqual(self.parser.parse(), ["PING"])

    def test_inline_no_cr(self) -> None:
        self.parser.feed(b"SET   k    v\n")
        self.assertEqual(self.parser.parse(), ["SET", "k", "v"])

    def test_empty_line(self) -> None:
        self.parser.feed(b"\r\n")
        self.assertEqual(self.parser.parse(), [])

    def test_empty_multibulk(self) -> None:
        self.parser.feed(b"*0\r\n")
        self.assertEqual(self.parser.parse(), [])

    def test_invalid_bulk_length(self) -> None:
        self.parser.feed(b"*1\r\n$-5\r\n")
        with self.assertRaises(ProtocolError):
            self.parser.parse()

    def test_missing_dollar(self) -> None:
        self.parser.feed(b"*1\r\n+PING\r\n")
        with self.assertRaises(ProtocolError):
            self.parser.parse()

    def test_feed_rejects_text(self) -> None:
        with self.assertRaises(TypeError):
            self.parser.feed("PING")  # type: ignore[arg-type]

    def test_large_inline_without_newline(self) -> None:
        self.parser.feed(b"x" * 70000)
        with self.assertRaises(ProtocolError):
            self.parser.parse()


class ReplyTests(unittest.TestCase):
    def _read(self, payload: bytes):
        return read_reply(io.BytesIO(payload))

    def test_simple(self) -> None:
        self.assertEqual(self._read(b"+OK\r\n"), ("simple", "OK"))

    def test_error(self) -> None:
        self.assertEqual(self._read(b"-ERR bad\r\n"), ("error", "ERR bad"))

    def test_integer(self) -> None:
        self.assertEqual(self._read(b":42\r\n"), ("integer", 42))

    def test_bulk(self) -> None:
        self.assertEqual(self._read(b"$2\r\nhi\r\n"), ("bulk", "hi"))
        self.assertEqual(self._read(b"$-1\r\n"), ("bulk", None))

    def test_array(self) -> None:
        kind, value = self._read(b"*2\r\n$4\r\nPING\r\n$-1\r\n")
        self.assertEqual(kind, "array")
        self.assertEqual(value, [("bulk", "PING"), ("bulk", None)])

    def test_array_nil(self) -> None:
        self.assertEqual(self._read(b"*-1\r\n"), ("array", None))

    def test_truncated(self) -> None:
        with self.assertRaises(ProtocolError):
            self._read(b"$5\r\nhi\r\n")


if __name__ == "__main__":
    unittest.main()
