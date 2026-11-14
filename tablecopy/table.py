"""内存模拟源表：带位点（LSN）的一致性快照和变更日志。"""

import threading
import time


class Table:
    """支持 copy 的快照/增量协议：snapshot() + changes_since(lsn)。

    snapshot_delay 模拟快照耗时，期间发生的写入进入变更日志，由增量补齐。
    """

    def __init__(self, rows=(), snapshot_delay=0.0):
        self._lock = threading.Lock()
        self._rows = {row["id"]: dict(row) for row in rows}
        self._lsn = 0
        self._log = []  # (lsn, op, row)，op 为 upsert/delete
        self.snapshot_delay = snapshot_delay

    def write(self, row):
        with self._lock:
            self._lsn += 1
            self._rows[row["id"]] = dict(row)
            self._log.append((self._lsn, "upsert", dict(row)))
        return self._lsn

    def delete(self, row_id):
        with self._lock:
            self._lsn += 1
            self._rows.pop(row_id, None)
            self._log.append((self._lsn, "delete", {"id": row_id}))

    def snapshot(self):
        """返回 (位点, 行列表)；位点之后的写入由 changes_since 补齐。"""
        with self._lock:
            lsn = self._lsn
            rows = [dict(r) for r in self._rows.values()]
        if self.snapshot_delay:
            time.sleep(self.snapshot_delay)
        return lsn, rows

    def changes_since(self, lsn):
        """返回 (当前位点, 位点之后的变更列表)。"""
        with self._lock:
            return self._lsn, [entry for entry in self._log if entry[0] > lsn]
