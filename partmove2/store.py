"""存储与清单：行级原子写入、内容摘要、原子切换清单。"""

import hashlib
import json
import threading


class StoreError(RuntimeError):
    """存储层错误。"""


class Store:
    """极简内存存储。

    path 代表一个分区路径（旧路径 / 新路径），内部是 {主键: 行}。
    单行的读写删都在锁内完成，不存在“半行”状态。
    """

    def __init__(self):
        self._data = {}
        self._lock = threading.RLock()

    def exists(self, path):
        with self._lock:
            return path in self._data

    def is_empty(self, path):
        with self._lock:
            return not self._data.get(path)

    def ensure(self, path):
        with self._lock:
            self._data.setdefault(path, {})

    def rows(self, path):
        """返回快照副本，调用方无法影响存储内部状态。"""
        with self._lock:
            return dict(self._data.get(path, {}))

    def row_count(self, path):
        with self._lock:
            return len(self._data.get(path, {}))

    def put(self, path, key, value):
        with self._lock:
            self._data.setdefault(path, {})[key] = value

    def delete(self, path, key):
        with self._lock:
            self._data.get(path, {}).pop(key, None)

    def clear(self, path):
        with self._lock:
            self._data[path] = {}

    def drop(self, path):
        with self._lock:
            self._data.pop(path, None)

    def digest(self, path):
        """对全部行做稳定摘要，用于内容校验。"""
        with self._lock:
            rows = self._data.get(path, {})
        payload = json.dumps(
            [[str(k), v] for k, v in sorted(rows.items(), key=lambda kv: str(kv[0]))],
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def diff(self, left, right):
        """对比两个路径，返回目标相对源缺失 / 多出 / 不一致的主键。"""
        with self._lock:
            lrows = dict(self._data.get(left, {}))
            rrows = dict(self._data.get(right, {}))
        return {
            "missing_in_target": sorted(str(k) for k in lrows if k not in rrows),
            "extra_in_target": sorted(str(k) for k in rrows if k not in lrows),
            "different": sorted(
                str(k) for k in lrows if k in rrows and lrows[k] != rrows[k]
            ),
        }


class Manifest:
    """分区 -> 路径的清单。

    切换只改一个指针（一次赋值 + 版本号自增），对外表现为原子操作：
    读请求要么解析到旧路径，要么解析到新路径，不会读到一半旧一半新。
    """

    def __init__(self):
        self._partitions = {}
        self._version = 0
        self._lock = threading.RLock()

    @property
    def version(self):
        with self._lock:
            return self._version

    def has(self, partition):
        with self._lock:
            return partition in self._partitions

    def register(self, partition, path):
        with self._lock:
            self._partitions.setdefault(partition, path)

    def resolve(self, partition):
        with self._lock:
            if partition not in self._partitions:
                raise KeyError("未知分区: %r" % (partition,))
            return self._partitions[partition]

    def resolve_or(self, partition, default=None):
        """单次加锁解析；不存在时返回 default。"""
        with self._lock:
            return self._partitions.get(partition, default)

    def paths(self):
        with self._lock:
            return dict(self._partitions)

    def switch(self, partition, path):
        """原子替换单个分区的路径，返回新的清单版本号。"""
        with self._lock:
            if partition not in self._partitions:
                raise KeyError("未知分区: %r" % (partition,))
            self._partitions[partition] = path
            self._version += 1
            return self._version
