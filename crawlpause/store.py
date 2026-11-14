"""按幂等键去重的结果存储。

恢复时检查点游标可能落在批次开头，批次内已写过的数据项会被重新抓取，
由本存储按幂等键挡下，保证同一个 key 只入库一次。
"""

import threading


class IdempotentStore:
    """线程安全的内存键值存储，``put`` 对重复键返回 ``False``。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def put(self, key, value):
        """写入一条结果。

        :returns: ``True`` 表示新写入；``False`` 表示幂等键已存在、被跳过。
        """
        with self._lock:
            if key in self._data:
                return False
            self._data[key] = value
            return True

    def get(self, key):
        with self._lock:
            return self._data.get(key)

    def keys(self):
        with self._lock:
            return list(self._data.keys())

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def size(self):
        with self._lock:
            return len(self._data)
