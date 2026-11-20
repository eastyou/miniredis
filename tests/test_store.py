from __future__ import annotations

import unittest
from typing import List

from miniredis.store import Store, WrongType


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = Store(clock=self.clock)

    def test_set_get_delete(self) -> None:
        self.store.set_string("k", "v")
        self.assertEqual(self.store.get_string("k"), "v")
        self.assertTrue(self.store.exists("k"))
        self.assertEqual(self.store.delete(["k", "missing"]), 1)
        self.assertIsNone(self.store.get_string("k"))

    def test_expiry(self) -> None:
        self.store.set_string("k", "v", expire_at=self.clock() + 10)
        self.assertEqual(self.store.ttl("k"), 10)
        self.clock.advance(9)
        self.assertEqual(self.store.ttl("k"), 1)
        self.assertEqual(self.store.get_string("k"), "v")
        self.clock.advance(2)
        self.assertIsNone(self.store.get_string("k"))
        self.assertEqual(self.store.ttl("k"), -2)
        self.assertEqual(self.store.dbsize(), 0)

    def test_set_expire_non_positive_deletes(self) -> None:
        self.store.set_string("k", "v")
        self.assertTrue(self.store.set_expire("k", 0))
        self.assertFalse(self.store.exists("k"))
        self.assertFalse(self.store.set_expire("missing", 10))

    def test_persist(self) -> None:
        self.store.set_string("k", "v")
        self.assertFalse(self.store.persist("k"))
        self.store.set_expire("k", 100)
        self.assertTrue(self.store.persist("k"))
        self.assertEqual(self.store.ttl("k"), -1)

    def test_incr_by(self) -> None:
        self.assertEqual(self.store.incr_by("n", 1), 1)
        self.assertEqual(self.store.incr_by("n", 10), 11)
        self.assertEqual(self.store.get_string("n"), "11")

    def test_incr_keeps_ttl(self) -> None:
        self.store.set_string("n", "1", expire_at=self.clock() + 5)
        self.store.incr_by("n", 1)
        self.assertEqual(self.store.ttl("n"), 5)

    def test_incr_non_integer(self) -> None:
        self.store.set_string("n", "abc")
        with self.assertRaises(ValueError):
            self.store.incr_by("n", 1)

    def test_wrong_type(self) -> None:
        self.store.list_push("l", ["a"], left=False)
        with self.assertRaises(WrongType):
            self.store.get_string("l")

    def test_keys_pattern(self) -> None:
        for key in ("user:1", "user:2", "order:1"):
            self.store.set_string(key, "x")
        self.assertEqual(sorted(self.store.keys("user:*")), ["user:1", "user:2"])
        self.assertEqual(len(self.store.keys()), 3)

    def test_list_push_and_range(self) -> None:
        self.store.list_push("l", ["a", "b"], left=False)
        self.store.list_push("l", ["c"], left=True)
        self.assertEqual(self.store.list_range("l", 0, -1), ["c", "a", "b"])
        self.assertEqual(self.store.list_range("l", 1, 1), ["a"])
        self.assertEqual(self.store.list_range("l", -2, -1), ["a", "b"])
        self.assertEqual(self.store.list_range("l", 5, 10), [])
        self.assertEqual(self.store.list_len("l"), 3)

    def test_lpush_order(self) -> None:
        self.store.list_push("l", ["a", "b", "c"], left=True)
        self.assertEqual(self.store.list_range("l", 0, -1), ["c", "b", "a"])

    def test_hash(self) -> None:
        self.assertEqual(self.store.hash_set("h", [("a", "1"), ("b", "2")]), 2)
        self.assertEqual(self.store.hash_set("h", [("a", "3")]), 0)
        self.assertEqual(self.store.hash_get("h", "a"), "3")
        self.assertEqual(self.store.hash_len("h"), 2)
        self.assertEqual(self.store.hash_del("h", ["a", "zzz"]), 1)
        self.assertEqual(self.store.hash_len("h"), 1)

    def test_set(self) -> None:
        self.assertEqual(self.store.set_add("s", ["a", "b", "a"]), 2)
        self.assertEqual(self.store.set_add("s", ["b", "c"]), 1)
        self.assertEqual(self.store.set_members("s"), ["a", "b", "c"])
        self.assertEqual(self.store.set_remove("s", ["a", "x"]), 1)
        self.assertEqual(self.store.set_len("s"), 2)

    def test_type_name(self) -> None:
        self.assertEqual(self.store.type_name("none"), "none")
        self.store.set_string("s", "v")
        self.store.list_push("l", ["v"], left=False)
        self.store.hash_set("h", [("f", "v")])
        self.store.set_add("set", ["v"])
        self.assertEqual(self.store.type_name("s"), "string")
        self.assertEqual(self.store.type_name("l"), "list")
        self.assertEqual(self.store.type_name("h"), "hash")
        self.assertEqual(self.store.type_name("set"), "set")

    def test_flush(self) -> None:
        self.store.set_string("a", "1")
        self.store.flush()
        self.assertEqual(self.store.dbsize(), 0)


if __name__ == "__main__":
    unittest.main()
