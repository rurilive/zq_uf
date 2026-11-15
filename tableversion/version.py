"""表版本与回滚。

- ``commit(table)``：每次提交生成一个不可变版本；版本元数据与数据分开存储。
- ``rollback(version)``：先校验版本链与数据校验和，再恢复目标版本的数据；
  回滚不删除任何版本，当前版本保留在链上。

版本号全局单调递增；每张表是一条链，``parent`` 指向上一个版本，
``committer`` 记录提交人，``structure`` 变更随版本一起记录。
"""

import copy
import hashlib
import json
import threading
import time

__all__ = [
    "commit",
    "rollback",
    "history",
    "current",
    "version_data",
    "verify_version",
    "archive_expired",
    "reset",
]

_DEFAULT_COMMITTER = "anonymous"

# 版本元数据与数据分开存放：元数据只放描述信息，数据单独放一份。
_meta = {}           # version -> metadata
_data = {}           # version -> {"rows": [...], "structure": {...} | None}
_archive_meta = {}   # 已归档版本的元数据
_archive_data = {}   # 已归档版本的数据

# 当前生效的数据与每张表的链头（回滚不会改链头，链头即当前版本）。
_tables = {}         # table -> {"rows": [...], "structure": {...} | None}
_heads = {}          # table -> 链上最新版本
_live_source = {}    # table -> 当前数据来自哪个版本

_next_version = 1


class _SerialQueue:
    """FIFO 队列：并发提交/回滚按到达顺序串行执行。"""

    def __init__(self):
        self._cond = threading.Condition()
        self._next_ticket = 0
        self._serving = 0

    def __enter__(self):
        with self._cond:
            ticket = self._next_ticket
            self._next_ticket += 1
            while ticket != self._serving:
                self._cond.wait()
        return self

    def __exit__(self, exc_type, exc, tb):
        with self._cond:
            self._serving += 1
            self._cond.notify_all()
        return False


_queue = _SerialQueue()


def _canonical(value):
    """稳定的 JSON 表示，保证同样的数据得到同样的校验和。"""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=repr
    )


def _checksum(rows, structure):
    payload = {"rows": rows, "structure": structure}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _structure_checksum(structure):
    if structure is None:
        return None
    return hashlib.sha256(_canonical(structure).encode("utf-8")).hexdigest()


def _chain(table):
    """返回仍可访问的版本链（从旧到新）。

    版本被归档后链路会在该处截断，因此链尾始终是当前版本。
    """
    versions = []
    version = _heads.get(table)
    while version is not None:
        meta = _meta.get(version)
        if meta is None:
            break
        versions.append(version)
        version = meta["parent"]
    versions.reverse()
    return versions


def _verify(version):
    """校验某个版本的数据与记录的校验和是否一致。"""
    meta = _meta.get(version)
    payload = _data.get(version)
    if meta is None or payload is None:
        return False
    return _checksum(payload["rows"], payload["structure"]) == meta["checksum"]


def _version_for_table(table, upto):
    """某张表在时间点 ``upto`` 之前（含）的最新可回滚版本。"""
    candidates = [
        version
        for version, meta in _meta.items()
        if meta["table"] == table and version <= upto
    ]
    return max(candidates) if candidates else None


def _failure(version, reason, chain=None, tables=None):
    archived = _archive_meta.get(version, {})
    return {
        "version": version,
        "rolled_back": False,
        "restored": 0,
        "checksum": _meta.get(version, archived).get("checksum"),
        "chain": list(chain or []),
        "tables": list(tables or []),
        "reason": reason,
    }


def commit(table):
    """提交一张表，生成新版本。

    ``table`` 支持 ``name``、``rows``、``structure``、``committer``。
    返回本次版本号、校验和、父版本、结构变更标记以及当前版本链。
    """
    if not isinstance(table, dict):
        raise TypeError("table 必须是 dict")

    name = table.get("name", "")
    rows = copy.deepcopy(list(table.get("rows", [])))
    structure = copy.deepcopy(table.get("structure"))
    committer = table.get("committer") or _DEFAULT_COMMITTER

    with _queue:
        global _next_version
        version = _next_version
        _next_version += 1

        parent = _heads.get(name)
        if parent is not None and parent in _meta:
            parent_structure_checksum = _meta[parent]["structure_checksum"]
        else:
            parent_structure_checksum = None

        checksum = _checksum(rows, structure)
        structure_checksum = _structure_checksum(structure)
        if parent is None:
            structure_changed = structure is not None
        else:
            structure_changed = structure_checksum != parent_structure_checksum

        meta = {
            "version": version,
            "table": name,
            "parent": parent,
            "committer": committer,
            "checksum": checksum,
            "structure_checksum": structure_checksum,
            "structure_changed": structure_changed,
            "rows": len(rows),
            "created_at": time.time(),
            "based_on": _live_source.get(name),
        }
        # 元数据与数据分开写入。
        _meta[version] = meta
        _data[version] = {"rows": rows, "structure": structure}

        _heads[name] = version
        _live_source[name] = version
        _tables[name] = {
            "rows": copy.deepcopy(rows),
            "structure": copy.deepcopy(structure),
        }
        chain = _chain(name)

        return {
            "table": name,
            "version": version,
            "parent": parent,
            "committer": committer,
            "checksum": checksum,
            "rows": len(rows),
            "structure": copy.deepcopy(structure),
            "structure_changed": structure_changed,
            "based_on": meta["based_on"],
            "chain": chain,
        }


def rollback(version, tables=None):
    """回滚到指定版本。

    默认只恢复该版本所属的表；``tables`` 可指定要恢复的部分表，
    每张表都恢复到 ``version`` 时点之前（含）的最新版本。
    回滚前校验目标版本在链上，并校验数据校验和；任何一步失败都不恢复数据。
    """
    with _queue:
        target = _meta.get(version)
        if target is None:
            reason = "archived" if version in _archive_meta else "unknown_version"
            return _failure(version, reason)

        name = target["table"]
        chain = _chain(name)
        if version not in chain:
            return _failure(version, "not_in_chain", chain=chain)

        # 回滚前校验整条仍可访问的版本链，防止元数据与数据不一致。
        for candidate in chain:
            if not _verify(candidate):
                return _failure(version, "checksum_mismatch", chain=chain)

        if tables is None:
            restore = {name: version}
        else:
            if isinstance(tables, str):
                tables = [tables]
            restore = {}
            for table in tables:
                found = _version_for_table(table, version)
                if found is None:
                    return _failure(
                        version, "no_version_for_table", chain=chain, tables=list(tables)
                    )
                restore[table] = found

        # 指定了其它表时，它们的目标版本同样要能通过完整性校验。
        for table, found in restore.items():
            if table == name and found == version:
                continue
            if not _verify(found) or found not in _chain(table):
                return _failure(version, "checksum_mismatch", chain=chain)

        restored_rows = 0
        for table, found in restore.items():
            payload = _data[found]
            _tables[table] = {
                "rows": copy.deepcopy(payload["rows"]),
                "structure": copy.deepcopy(payload["structure"]),
            }
            _live_source[table] = found
            restored_rows += len(payload["rows"])

        # 链头和所有版本都保留，回滚后仍可回滚回来或继续提交。
        return {
            "version": version,
            "rolled_back": True,
            "restored": restored_rows,
            "checksum": target["checksum"],
            "chain": chain,
            "tables": list(restore),
            "kept_version": _heads.get(name),
        }


def history(table=None):
    """返回版本的元数据列表（从旧到新），可按表过滤。"""
    versions = sorted(_meta)
    return [
        dict(_meta[version])
        for version in versions
        if table is None or _meta[version]["table"] == table
    ]


def current(table=None):
    """返回当前生效的数据副本；不传表名时返回所有表。"""
    if table is None:
        return {name: copy.deepcopy(value) for name, value in _tables.items()}
    if table not in _tables:
        return None
    return copy.deepcopy(_tables[table])


def version_data(version):
    """返回某个版本的 rows/structure 数据副本。"""
    payload = _data.get(version)
    return copy.deepcopy(payload) if payload is not None else None


def verify_version(version):
    """公开的数据完整性校验。"""
    return _verify(version)


def archive_expired(max_age_seconds, now=None):
    """归档超过指定存续时间的版本；每张表的当前版本（链头）不归档。

    返回被归档的版本号列表。归档后的版本不能再回滚。
    """
    now = time.time() if now is None else now
    archived = []
    with _queue:
        heads = set(_heads.values())
        for version in sorted(_meta):
            if version in heads:
                continue
            if now - _meta[version]["created_at"] >= max_age_seconds:
                _archive_meta[version] = _meta.pop(version)
                _archive_data[version] = _data.pop(version)
                archived.append(version)
    return archived


def reset():
    """清空所有状态（测试与演示用）。"""
    global _next_version
    with _queue:
        _meta.clear()
        _data.clear()
        _archive_meta.clear()
        _archive_data.clear()
        _tables.clear()
        _heads.clear()
        _live_source.clear()
        _next_version = 1
