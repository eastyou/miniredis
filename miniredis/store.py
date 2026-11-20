"""内存数据结构与过期时间处理。

所有值都存放在 :class:`Entry` 中，读取时惰性判断是否过期，避免额外的清理线程。
时钟通过构造函数注入，测试里用假时钟就能确定性地验证 TTL 行为。
"""

from __future__ import annotations

import fnmatch
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence


class WrongType(Exception):
    """对 key 执行了不匹配其数据类型的操作。"""


class Entry:
    """一条键值记录：值本身加上可选的绝对过期时间戳（秒）。"""

    __slots__ = ("value", "expire_at")

    def __init__(self, value: Any, expire_at: Optional[float] = None) -> None:
        self.value: Any = value
        self.expire_at: Optional[float] = expire_at


class Store:
    """单数据库的内存 KV 存储，支持 string / list / hash / set。"""

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._data: Dict[str, Entry] = {}
        self._clock: Callable[[], float] = clock or time.time

    def now(self) -> float:
        return self._clock()

    def _live_entry(self, key: str) -> Optional[Entry]:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expire_at is not None and entry.expire_at <= self.now():
            del self._data[key]
            return None
        return entry

    def _typed_entry(self, key: str, expected: type) -> Optional[Entry]:
        entry = self._live_entry(key)
        if entry is None:
            return None
        if not isinstance(entry.value, expected):
            raise WrongType(key)
        return entry

    def _ensure_typed(self, key: str, expected: type, factory: Callable[[], Any]) -> Any:
        entry = self._typed_entry(key, expected)
        if entry is None:
            value = factory()
            self._data[key] = Entry(value)
            return value
        return entry.value

    # ---- 通用 ----

    def exists(self, key: str) -> bool:
        return self._live_entry(key) is not None

    def delete(self, keys: Sequence[str]) -> int:
        removed = 0
        for key in keys:
            if self._live_entry(key) is not None:
                del self._data[key]
                removed += 1
        return removed

    def keys(self, pattern: str = "*") -> List[str]:
        alive = [key for key in list(self._data) if self._live_entry(key) is not None]
        if pattern == "*":
            return alive
        return [key for key in alive if fnmatch.fnmatchcase(key, pattern)]

    def dbsize(self) -> int:
        return len([key for key in list(self._data) if self._live_entry(key) is not None])

    def flush(self) -> None:
        self._data.clear()

    def type_name(self, key: str) -> str:
        entry = self._live_entry(key)
        if entry is None:
            return "none"
        value = entry.value
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "hash"
        if isinstance(value, set):
            return "set"
        return "unknown"

    # ---- 过期 ----

    def set_expire(self, key: str, seconds: int) -> bool:
        entry = self._live_entry(key)
        if entry is None:
            return False
        if seconds <= 0:
            del self._data[key]
            return True
        entry.expire_at = self.now() + seconds
        return True

    def ttl(self, key: str) -> int:
        entry = self._live_entry(key)
        if entry is None:
            return -2
        if entry.expire_at is None:
            return -1
        remaining = int(entry.expire_at - self.now())
        return remaining if remaining > 0 else 0

    def persist(self, key: str) -> bool:
        entry = self._live_entry(key)
        if entry is None or entry.expire_at is None:
            return False
        entry.expire_at = None
        return True

    # ---- string ----

    def get_string(self, key: str) -> Optional[str]:
        entry = self._typed_entry(key, str)
        return None if entry is None else entry.value

    def set_string(self, key: str, value: str, expire_at: Optional[float] = None) -> None:
        self._data[key] = Entry(value, expire_at)

    def incr_by(self, key: str, delta: int) -> int:
        entry = self._typed_entry(key, str)
        expire_at: Optional[float] = None
        current = 0
        if entry is not None:
            try:
                current = int(entry.value)
            except ValueError:
                raise ValueError("value is not an integer") from None
            expire_at = entry.expire_at
        current += delta
        self._data[key] = Entry(str(current), expire_at)
        return current

    # ---- list ----

    def list_push(self, key: str, values: Sequence[str], left: bool) -> int:
        items = self._ensure_typed(key, list, list)
        if left:
            items[0:0] = list(reversed(values))
        else:
            items.extend(values)
        return len(items)

    def list_range(self, key: str, start: int, stop: int) -> List[str]:
        entry = self._typed_entry(key, list)
        if entry is None:
            return []
        items: List[str] = entry.value
        size = len(items)
        if start < 0:
            start += size
        if stop < 0:
            stop += size
        start = max(start, 0)
        stop = min(stop, size - 1)
        if size == 0 or start > stop or start >= size:
            return []
        return list(items[start : stop + 1])

    def list_len(self, key: str) -> int:
        entry = self._typed_entry(key, list)
        return 0 if entry is None else len(entry.value)

    # ---- hash ----

    def hash_set(self, key: str, pairs: Iterable[Sequence[str]]) -> int:
        mapping = self._ensure_typed(key, dict, dict)
        added = 0
        for pair in pairs:
            field, value = pair[0], pair[1]
            if field not in mapping:
                added += 1
            mapping[field] = value
        return added

    def hash_get(self, key: str, field: str) -> Optional[str]:
        entry = self._typed_entry(key, dict)
        if entry is None:
            return None
        return entry.value.get(field)

    def hash_del(self, key: str, fields: Sequence[str]) -> int:
        entry = self._typed_entry(key, dict)
        if entry is None:
            return 0
        mapping: Dict[str, str] = entry.value
        removed = 0
        for field in fields:
            if field in mapping:
                del mapping[field]
                removed += 1
        return removed

    def hash_len(self, key: str) -> int:
        entry = self._typed_entry(key, dict)
        return 0 if entry is None else len(entry.value)

    # ---- set ----

    def set_add(self, key: str, members: Sequence[str]) -> int:
        bucket = self._ensure_typed(key, set, set)
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    def set_remove(self, key: str, members: Sequence[str]) -> int:
        entry = self._typed_entry(key, set)
        if entry is None:
            return 0
        bucket: set = entry.value
        removed = 0
        for member in members:
            if member in bucket:
                bucket.remove(member)
                removed += 1
        return removed

    def set_members(self, key: str) -> List[str]:
        entry = self._typed_entry(key, set)
        if entry is None:
            return []
        return sorted(entry.value)

    def set_len(self, key: str) -> int:
        entry = self._typed_entry(key, set)
        return 0 if entry is None else len(entry.value)
