"""版本化表：提交生成版本链，回滚按校验和校验后恢复。

设计口径
========
* 每次非空提交为该表生成一个递增整数版本（从 1 开始），空提交不生成版本；
* 每个版本记录：表名、版本号、父版本、提交人、时间戳、结构（schema）、
  结构变更、校验和、行数；
* 行数据单独写数据区（见 :mod:`tableversion.storage`），元数据不含行内容；
* 校验和 = 对规范 JSON（键排序、无空白）取 SHA-256；
* 回滚前先校验整条存活版本链（版本连续、父指针正确、数据校验和一致），
  再恢复数据；回滚不删除任何历史版本，当前版本被保留；
* 所有提交/回滚/归档操作进入同一条 FIFO 队列，由单一工作线程串行执行，
  因此并发提交严格按到达顺序取得连续版本号；
* 过期版本可归档，归档版本不再参与版本链，也不能作为回滚目标。
"""

import hashlib
import json
import os
import queue
import threading
import datetime

from .storage import FileStorage, MemoryStorage, _clone, _safe_name


def _canonical(obj):
    """规范 JSON：键排序、无多余空白，保证校验和可复现。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum_of(rows):
    """对行数据计算 SHA-256 校验和。"""
    return hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest()


def _infer_schema(rows):
    """从行数据推断结构。

    dict 行 → 列名并集（按首次出现顺序）；其它行 → 单列表，类型为行类型名。
    """
    columns = []
    seen = set()
    for row in rows:
        if isinstance(row, dict):
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append({"name": str(key), "type": "any"})
        else:
            return [{"name": "value", "type": type(row).__name__}]
    return columns


def _normalize_schema(table, rows):
    """取显式 schema；没有则从数据推断。"""
    schema = table.get("schema", table.get("columns"))
    if schema is None:
        return _infer_schema(rows)
    if isinstance(schema, dict):
        schema = schema.get("columns", [])
    normalized = []
    for col in schema:
        if isinstance(col, dict):
            normalized.append({
                "name": str(col.get("name")),
                "type": str(col.get("type", "any")),
            })
        else:
            normalized.append({"name": str(col), "type": "any"})
    return normalized


def _schema_diff(old_schema, new_schema):
    """返回结构变更；结构无变化时返回空字典。"""
    old_cols = [c["name"] for c in (old_schema or [])]
    new_cols = [c["name"] for c in (new_schema or [])]
    added = [c for c in new_cols if c not in old_cols]
    removed = [c for c in old_cols if c not in new_cols]
    changed = []
    old_types = {c["name"]: c["type"] for c in (old_schema or [])}
    for col in new_schema or []:
        if col["name"] in old_types and old_types[col["name"]] != col["type"]:
            changed.append({
                "name": col["name"],
                "from": old_types[col["name"]],
                "to": col["type"],
            })
    if not added and not removed and not changed:
        return {}
    return {"added": added, "removed": removed, "changed": changed}


def _default_committer():
    return os.environ.get("TABLEVERSION_USER") or os.environ.get("USER") or "unknown"


class IntegrityError(Exception):
    """版本链或数据校验和校验失败。"""


class TableVersion:
    """版本化表存储，支持内存后端或目录后端。"""

    def __init__(self, root=None, committer=None):
        self.storage = FileStorage(root) if root else MemoryStorage()
        self.default_committer = committer or _default_committer()
        self._queue = queue.Queue()
        self._closed = False
        self._worker = threading.Thread(
            target=self._run, name="tableversion-worker", daemon=True
        )
        self._worker.start()

    # -- 串行队列 -----------------------------------------------------
    def _run(self):
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            done, fn, args, kwargs, box = item
            try:
                box["result"] = fn(*args, **kwargs)
            except Exception as exc:  # 原样回传给调用线程
                box["error"] = exc
            finally:
                done.set()
                self._queue.task_done()

    def _submit(self, fn, *args, **kwargs):
        if self._closed:
            raise RuntimeError("TableVersion 已关闭")
        done = threading.Event()
        box = {}
        self._queue.put((done, fn, args, kwargs, box))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box["result"]

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._worker.join(timeout=5)

    # -- 提交 ---------------------------------------------------------
    def commit(self, table, committer=None):
        return self._submit(self._do_commit, table, committer)

    def _do_commit(self, table, committer):
        name = _safe_name(table.get("name", ""))
        rows = _clone(table.get("rows", []))
        state = self.storage.state
        state["last_table"] = name

        # 空提交：不生成版本，保持历史不被空快照污染。
        if not rows:
            return {
                "table": name,
                "version": None,
                "rows": 0,
                "checksum": None,
                "parent": state["heads"].get(name),
                "committer": committer or self.default_committer,
                "schema": _normalize_schema(table, rows),
                "schema_changed": {},
                "chain": self._chain_view(name),
            }

        parent = state["heads"].get(name)
        version = state["counters"].get(name, 0) + 1
        schema = _normalize_schema(table, rows)
        old_schema = None
        if parent is not None:
            old_schema = self.storage.read_meta(name, parent).get("schema")
        changed = _schema_diff(old_schema, schema)
        digest = checksum_of(rows)

        payload = {"rows": rows}
        meta = {
            "table": name,
            "version": version,
            "parent": parent,
            "committer": committer or self.default_committer,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "schema": schema,
            "schema_changed": changed,
            "checksum": digest,
            "rows": len(rows),
        }

        # 先写数据，再写元数据，再更新指针：崩溃时不会出现指向不完整数据的版本。
        self.storage.write_data(name, version, payload)
        self.storage.write_meta(name, version, meta)
        state["counters"][name] = version
        state["heads"][name] = version
        self.storage.save_state()

        return {
            "table": name,
            "version": version,
            "rows": len(rows),
            "checksum": digest,
            "parent": parent,
            "committer": meta["committer"],
            "schema": schema,
            "schema_changed": changed,
            "chain": self._chain_view(name),
        }

    # -- 版本链与校验 -------------------------------------------------
    def _chain_view(self, table):
        """从当前 head 回溯父版本，生成链视图（新→旧）。

        父版本若已归档，存活链在归档边界处终止。
        """
        chain = []
        current = self.storage.state["heads"].get(table)
        while current is not None:
            if self.storage.is_archived(table, current):
                break
            meta = self.storage.read_meta(table, current)
            chain.append({
                "version": current,
                "parent": meta["parent"],
                "committer": meta["committer"],
                "checksum": meta["checksum"],
                "timestamp": meta["timestamp"],
            })
            current = meta["parent"]
        return chain

    def validate(self, table):
        """校验存活版本链，返回链视图；发现问题抛 :class:`IntegrityError`。"""
        return self._submit(self._do_validate, table)

    def _do_validate(self, table):
        name = _safe_name(table)
        versions = self.storage.list_versions(name)
        if not versions:
            return []
        # 允许前缀版本已归档：存活版本自身连续即可（如 [3, 4]）。
        first = versions[0]
        if versions != list(range(first, versions[-1] + 1)):
            raise IntegrityError("表 %s 版本号不连续: %r" % (name, versions))

        expected_parent = None if first == 1 else first - 1
        for v in versions:
            meta = self.storage.read_meta(name, v)
            if meta.get("parent") != expected_parent:
                raise IntegrityError(
                    "表 %s 版本 %s 父指针错误: 期望 %r，实际 %r"
                    % (name, v, expected_parent, meta.get("parent"))
                )
            payload = self.storage.read_data(name, v)
            actual = checksum_of(payload.get("rows", []))
            if actual != meta.get("checksum"):
                raise IntegrityError(
                    "表 %s 版本 %s 校验和不一致: 元数据 %s，实际 %s"
                    % (name, v, meta.get("checksum"), actual)
                )
            expected_parent = v

        head = self.storage.state["heads"].get(name)
        if head != versions[-1]:
            raise IntegrityError(
                "表 %s head 指针错误: 期望 %s，实际 %s" % (name, versions[-1], head)
            )
        return self._chain_view(name)

    # -- 回滚 ---------------------------------------------------------
    def rollback(self, version, table=None, tables=None):
        return self._submit(self._do_rollback, version, table, tables)

    def _do_rollback(self, version, table, tables):
        if tables is not None:
            targets = [_safe_name(t) for t in tables]
        else:
            target = table or self.storage.state.get("last_table")
            targets = [_safe_name(target)] if target else []
        if not targets:
            # 从未提交过或未指定表：没有可回滚目标，按失败返回。
            return {
                "version": version,
                "rolled_back": False,
                "restored": 0,
                "tables": [],
                "chain": [],
                "reason": "没有可回滚的表",
            }

        details = []
        missing = {}
        # 先对所有目标表做完整性校验，任一失败（除“版本不存在”）则整体不写任何数据。
        for name in targets:
            if self.storage.is_archived(name, version):
                raise IntegrityError(
                    "表 %s 版本 %s 已归档，不能回滚（请先从归档区恢复）" % (name, version)
                )
            existing = self.storage.list_versions(name)
            if version not in existing:
                missing[name] = "版本不存在"
                continue
            self._do_validate(name)

        ok = not missing
        primary_chain = []
        for index, name in enumerate(targets):
            if name in missing:
                details.append({
                    "table": name,
                    "version": version,
                    "restored": 0,
                    "rolled_back": False,
                    "reason": missing[name],
                })
                continue
            meta = self.storage.read_meta(name, version)
            payload = self.storage.read_data(name, version)
            restored = _clone(payload.get("rows", []))

            # 恢复当前表数据：写一个新的“回滚恢复点”，历史版本全部保留。
            parent = self.storage.state["heads"][name]
            new_version = self.storage.state["counters"].get(name, 0) + 1
            digest = checksum_of(restored)
            recovery_meta = {
                "table": name,
                "version": new_version,
                "parent": parent,
                "committer": self.default_committer,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "schema": meta.get("schema", []),
                "schema_changed": {},
                "checksum": digest,
                "rows": len(restored),
                "rollback_of": version,
            }
            self.storage.write_data(name, new_version, {"rows": restored})
            self.storage.write_meta(name, new_version, recovery_meta)
            self.storage.state["counters"][name] = new_version
            self.storage.state["heads"][name] = new_version
            self.storage.save_state()

            details.append({
                "table": name,
                "version": version,
                "restored": len(restored),
                "rolled_back": True,
                "checksum": digest,
                "recovery_version": new_version,
                "schema": recovery_meta["schema"],
            })
            if index == 0:
                primary_chain = self._chain_view(name)

        return {
            "version": version,
            "rolled_back": ok,
            "restored": sum(d["restored"] for d in details if d.get("rolled_back")),
            "tables": details,
            "chain": primary_chain,
        }

    # -- 归档 ---------------------------------------------------------
    def archive(self, keep=1, table=None):
        return self._submit(self._do_archive, keep, table)

    def _do_archive(self, keep, table):
        if keep < 0:
            raise ValueError("keep 不能为负数")
        names = [_safe_name(table)] if table else list(self.storage.state["counters"])
        moved = []
        for name in names:
            versions = self.storage.list_versions(name)
            expired = versions[:-keep] if keep else list(versions)
            for v in expired:
                self.storage.archive(name, v)
                moved.append({"table": name, "version": v})
            # head 始终指向存活版本；全部归档后 head 置空。
            surviving = self.storage.list_versions(name)
            self.storage.state["heads"][name] = surviving[-1] if surviving else None
        self.storage.save_state()
        return {"archived": moved, "count": len(moved)}

    def list_archived(self, table=None):
        return self._submit(self._do_list_archived, table)

    def _do_list_archived(self, table):
        return self.storage.list_archived(table)

    # -- 查询 ---------------------------------------------------------
    def history(self, table):
        return self._submit(self._do_history, table)

    def _do_history(self, table):
        return self._chain_view(_safe_name(table))

    def head(self, table):
        return self._submit(self._do_head, table)

    def _do_head(self, table):
        name = _safe_name(table)
        head = self.storage.state["heads"].get(name)
        if head is None:
            return None
        meta = self.storage.read_meta(name, head)
        payload = self.storage.read_data(name, head)
        return {
            "table": name,
            "version": head,
            "rows": _clone(payload.get("rows", [])),
            "checksum": meta["checksum"],
            "schema": meta.get("schema", []),
            "committer": meta["committer"],
            "timestamp": meta["timestamp"],
        }


# ---------------------------------------------------------------------------
# 模块级门面：保持历史接口 commit(table) / rollback(version) 不变。
# 测试或多实例场景可直接使用 TableVersion 类获得隔离存储。
# ---------------------------------------------------------------------------

_default = TableVersion()


def reset():
    """重置模块级默认存储（主要供测试隔离使用）。"""
    global _default
    _default.close()
    _default = TableVersion()


def commit(table, committer=None):
    """提交一张表，返回版本、校验和与版本链。"""
    return _default.commit(table, committer=committer)


def rollback(version, table=None, tables=None):
    """回滚到指定版本；可指定单表或部分表列表。"""
    return _default.rollback(version, table=table, tables=tables)
