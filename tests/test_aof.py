from __future__ import annotations

import os
import tempfile
import unittest

from miniredis.aof import Aof, remove, replay
from miniredis.commands import Executor
from miniredis.protocol import encode_command
from miniredis.store import Store


class AofTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "data.aof")

    def test_append_only_mutating_commands(self) -> None:
        store = Store()
        with Aof(self.path) as aof:
            executor = Executor(store, aof)
            executor.execute(["SET", "k", "v"])
            executor.execute(["RPUSH", "l", "a", "b"])
            executor.execute(["GET", "k"])
            executor.execute(["PING"])
        with open(self.path, "rb") as handle:
            raw = handle.read()
        self.assertEqual(raw.count(b"*"), 2)

    def test_replay_restores_state(self) -> None:
        store = Store()
        with Aof(self.path) as aof:
            executor = Executor(store, aof)
            executor.execute(["SET", "k", "v"])
            executor.execute(["RPUSH", "l", "a", "b"])
            executor.execute(["HSET", "h", "f", "1"])

        restored = Store()
        count = replay(self.path, Executor(restored))
        self.assertEqual(count, 3)
        self.assertEqual(restored.get_string("k"), "v")
        self.assertEqual(restored.list_range("l", 0, -1), ["a", "b"])
        self.assertEqual(restored.hash_get("h", "f"), "1")

    def test_replay_missing_file(self) -> None:
        self.assertEqual(replay(self.path, Executor(Store())), 0)

    def test_replay_ignores_truncated_tail(self) -> None:
        with open(self.path, "wb") as handle:
            handle.write(encode_command(["SET", "a", "1"]))
            handle.write(b"*2\r\n$3\r\nSET\r\n$1\r\nb")
        restored = Store()
        count = replay(self.path, Executor(restored))
        self.assertEqual(count, 1)
        self.assertEqual(restored.get_string("a"), "1")
        self.assertIsNone(restored.get_string("b"))

    def test_replay_ignores_corrupt_data(self) -> None:
        with open(self.path, "wb") as handle:
            handle.write(b"*1\r\n$-9\r\n")
        self.assertEqual(replay(self.path, Executor(Store())), 0)

    def test_creates_parent_directory(self) -> None:
        nested = os.path.join(self._tmp.name, "a", "b", "data.aof")
        with Aof(nested) as aof:
            aof.append(["SET", "k", "v"])
        self.assertTrue(os.path.exists(nested))

    def test_remove(self) -> None:
        with Aof(self.path) as aof:
            aof.append(["SET", "k", "v"])
        remove(self.path)
        self.assertFalse(os.path.exists(self.path))
        remove(self.path)  # 重复删除不应报错


if __name__ == "__main__":
    unittest.main()
