"""按版本收敛的变更合并。

每条变更携带 key（主键）、version（版本）、txn（事务号）和 seq
（事务内序号）。同一主键只保留最高版本，删除以墓碑形式保留，重复事件按
(txn, seq) 去重，同一事务在收到 commit 标记后才整体可见。
"""


class Merger:
    def __init__(self):
        # key -> {"version": 版本号, "deleted": 是否墓碑, "value": 行值}
        self._entries = {}
        # txn -> {"events": 缓冲事件, "seen": 已见序号}
        self._pending = {}
        self._committed_txns = set()

    def apply(self, events):
        if isinstance(events, dict):
            events = (events,)
        for event in events:
            if event.get("op") == "commit":
                self._commit(event.get("txn"))
            else:
                self._apply_change(event)
        return self.snapshot()

    def _apply_change(self, event):
        txn = event.get("txn")
        seq = event.get("seq")

        # 重复投递幂等：同一事务的同一序号只处理一次。
        if txn is not None and seq is not None:
            if txn in self._committed_txns:
                return
            pending = self._pending.setdefault(
                txn, {"events": [], "seen": set()}
            )
            if seq in pending["seen"]:
                return
            pending["seen"].add(seq)
            pending["events"].append(event)
        else:
            # 无事务号的事件（兼容旧格式）立即生效。
            self._merge(event)

    def _commit(self, txn):
        if txn is None or txn in self._committed_txns:
            return
        pending = self._pending.pop(txn, None)
        self._committed_txns.add(txn)
        if pending:
            for event in pending["events"]:
                self._merge(event)

    def _merge(self, event):
        key = event.get("key")
        version = event.get("version", 0)
        incoming = {
            "version": version,
            "deleted": event.get("op") == "delete",
            "value": event.get("value"),
        }
        current = self._entries.get(key)
        # 旧版本（含旧删除）不能覆盖新版本，墓碑因此不会被晚到事件复活。
        if current is None or version >= current["version"]:
            self._entries[key] = incoming

    def snapshot(self):
        return {
            key: entry["value"]
            for key, entry in self._entries.items()
            if not entry["deleted"]
        }
