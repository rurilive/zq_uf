"""快照与变更流按位点对齐。

协议（内存模拟，真实系统中变更流对应 binlog / CDC 日志）：

* 每个变更带单调递增的位点 position，从 0 开始，只追加；
* snapshot 记录开始位点 start_position，快照期间的新变更先进入缓存；
* resume 按位点顺序应用缓存，位点只向前推进，失败停在原位点；
* upsert 按主键覆盖、delete 缺行即无操作，整条日志重放结果一致（幂等）；
* 应用失败抛 AlignError，位点不动；repair 原位点修复事件后 resume 继续；
* verify 按表比对行数与内容，差异逐表返回。
"""

from copy import deepcopy

DEFAULT_TABLE = "default"


class AlignError(Exception):
    """变更应用失败。位点不推进，修复事件后可 resume。"""

    def __init__(self, message, *, event, position):
        super().__init__(message)
        self.event = event
        self.position = position


class ChangeStream:
    """变更日志：append 只追加，read_since 按位点读取，replace 原位点修复。"""

    def __init__(self):
        self._events = []

    @property
    def head(self):
        """下一个位点（= 已追加事件数）。"""
        return len(self._events)

    def append(self, event):
        logged = dict(event)
        logged.setdefault("position", self.head)
        self._events.append(logged)
        return logged["position"]

    def read_since(self, position):
        """返回位点 >= position 的事件副本，按位点顺序。"""
        return [deepcopy(e) for e in self._events[position:]]

    def at(self, position):
        return deepcopy(self._events[position])

    def replace(self, position, event):
        """原位点修复事件：位点不变，只替换内容。"""
        fixed = dict(event)
        fixed["position"] = position
        self._events[position] = fixed


def _is_bare_table(obj):
    """旧格式 {"rows": [...]} 单表，区别于 {表名: {"rows": [...]}} 库。"""
    return (
        isinstance(obj, dict)
        and "rows" in obj
        and not any(isinstance(v, dict) and "rows" in v for v in obj.values())
    )


def _apply_mutation(tables, event, *, position):
    """把一条变更应用到表集合；upsert/delete 均幂等。"""
    op = event.get("op")
    # 先校验，再动表：坏事件不得留下幽灵空表
    if op not in ("upsert", "delete"):
        raise AlignError(
            f"未知或失败的变更类型 op={op!r}", event=event, position=position
        )
    row = event.get("row")
    if not isinstance(row, dict) or "id" not in row:
        raise AlignError(f"事件缺少主键 id: {event}", event=event, position=position)
    table_name = event.get("table", DEFAULT_TABLE)
    target = tables.setdefault(table_name, {"rows": []})
    if op == "upsert":
        for i, existing in enumerate(target["rows"]):
            if existing.get("id") == row["id"]:
                target["rows"][i] = deepcopy(row)
                break
        else:
            target["rows"].append(deepcopy(row))
    elif op == "delete":
        target["rows"] = [r for r in target["rows"] if r.get("id") != row["id"]]


def write(store, stream, event):
    """生产侧写库：变更先入流拿位点，再落库。

    坏事件（op 非法或缺主键）只留在流里，模拟日志损坏——生产库不受影响，
    恢复端应用到该位点时会停下等待 repair。
    """
    position = stream.append(event)
    logged = stream.at(position)
    if logged.get("op") in ("upsert", "delete") and isinstance(
        logged.get("row"), dict
    ) and "id" in logged["row"]:
        _apply_mutation(store, logged, position=position)
    return position


def repair(store, stream, position, event):
    """修复坏事件：原位点替换日志，并把修正后的变更补到生产库。"""
    stream.replace(position, event)
    _apply_mutation(store, stream.at(position), position=position)


def _sorted_rows(rows):
    return sorted((deepcopy(r) for r in rows), key=lambda r: r.get("id"))


class SnapshotSession:
    """一次快照 + 增量追平的恢复会话，持有快照副本和当前位点。"""

    def __init__(self, store, stream, start=None):
        self._store = store
        self._stream = stream
        self.start_position = stream.head if start is None else start
        self.position = self.start_position
        self.tables = {name: deepcopy(table) for name, table in store.items()}
        self.error = None

    @classmethod
    def begin(cls, store, stream):
        """开始快照：记录当前流位点，拷一份表数据。"""
        return cls(store, stream)

    @property
    def cached(self):
        """快照之后已进入变更流、尚未应用的事件数。"""
        return self._stream.head - self.position

    def cached_events(self):
        return self._stream.read_since(self.position)

    def resume(self):
        """按位点顺序应用缓存变更，直到追平或遇到坏事件。

        成功返回结果字典；坏事件抛 AlignError，位点停在该事件处，
        已应用的不回滚、未应用的保留在缓存中，修复后再次 resume 即可继续。
        """
        applied = skipped = 0
        self.error = None
        for event in self._stream.read_since(self.position):
            pos = event["position"]
            if pos < self.position:
                # 防御：位点只向前，旧位点事件跳过且不影响结果
                skipped += 1
                continue
            try:
                _apply_mutation(self.tables, event, position=pos)
            except AlignError as exc:
                self.error = str(exc)
                raise
            self.position = pos + 1
            applied += 1
        return self.result(applied=applied, skipped=skipped)

    def diffs(self):
        """按表比对会话副本与生产库当前内容：先比行数，再比行内容。"""
        out = []
        for name in sorted(set(self.tables) | set(self._store)):
            got_rows = self.tables.get(name, {"rows": []})["rows"]
            want_rows = self._store.get(name, {"rows": []})["rows"]
            if len(got_rows) != len(want_rows):
                out.append(
                    {
                        "table": name,
                        "kind": "row_count",
                        "session": len(got_rows),
                        "source": len(want_rows),
                    }
                )
                continue
            got, want = _sorted_rows(got_rows), _sorted_rows(want_rows)
            if got != want:
                out.append(
                    {
                        "table": name,
                        "kind": "content",
                        "session": got,
                        "source": want,
                    }
                )
        return out

    def verify(self):
        """追平后校验：返回含逐表差异的结果字典。"""
        return self.result()

    def result(self, *, applied=0, skipped=0):
        caught_up = self.position == self._stream.head and self.error is None
        diffs = self.diffs()
        return {
            "ok": caught_up and not diffs,
            "position": self.position,
            "start_position": self.start_position,
            "cached": self._stream.head - self.position,
            "applied": applied,
            "skipped": skipped,
            "error": self.error,
            "caught_up": caught_up,
            "diffs": diffs,
        }


def snapshot(table_or_store, stream=None):
    """开始快照，返回 SnapshotSession。

    兼容旧的单表入参 {"rows": [...]}；多表传 {表名: {"rows": [...]}}。
    """
    store = (
        {DEFAULT_TABLE: table_or_store}
        if _is_bare_table(table_or_store)
        else table_or_store
    )
    return SnapshotSession.begin(store, stream or ChangeStream())


def apply(events, store=None, stream=None):
    """便捷封装：快照空库（或给定库）后把一批变更追平，返回结果字典。"""
    stream = stream or ChangeStream()
    store = {} if store is None else store
    session = snapshot(store, stream)
    for event in events:
        write(store, stream, event)
    return session.resume()
