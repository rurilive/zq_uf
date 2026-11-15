"""表复制：一致性快照 + 增量补偿 + 断点续传 + 行数/内容校验。

核心流程：
1. 取一致性快照（版本位点），把源表在该位点的状态定住；
2. 按检查点分批复制快照数据，失败只留暂存区（对外不可查），重试从断点继续；
3. 补偿复制期间源表的变更（insert/update/delete），追上当前位点；
4. 发布目标表（此后可立即查询），比较两侧行数与内容校验和；
5. 校验不一致则停止并保留两侧数据，快照位点与差异均可查询。

数据源是带版本日志的内存表（``Table`` / ``ReadOnlyTable``），也接受普通
dict（``{"name", "rows"}``），后者会包装成只读表，重跑时按表名复用。
"""

import copy as _copy
import hashlib
import json

__all__ = [
    "Table",
    "ReadOnlyTable",
    "CopyError",
    "ReadOnlyError",
    "Snapshot",
    "CopyResult",
    "copy",
    "verify",
    "query",
    "abort",
    "snapshot_of",
    "reset",
]


class CopyError(Exception):
    """复制过程中的错误（含测试/演示用的故障注入）。"""


class ReadOnlyError(CopyError):
    """对只读表执行写入。"""


def _canonical(value):
    """JSON 规范序列化，键序固定，校验和才可复现。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _checksum(rows):
    """内容校验和：对每行 (id, data) 的规范表示整体做 sha256。"""
    h = hashlib.sha256()
    for rid, data in sorted(rows.items(), key=lambda kv: kv[0]):
        h.update(_canonical([rid, data]).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


class Table:
    """带版本日志的内存表。

    每次写入（insert/update/delete）产生一条变更日志并把版本号 +1。
    快照位点即版本号：``snapshot()`` 之后，无论表再怎么写，都可以用
    ``state_at(version)`` 还原该位点的完整数据，从而"定住一个版本"。
    """

    def __init__(self, name, rows=(), readonly=False):
        self.name = name
        self._readonly = readonly
        self._rows = {}          # id -> data
        self._changes = []       # [{version, op, id, data?}]
        self._version = 0
        for row in rows:
            self._apply_insert(row)  # 构造装载不经只读守卫

    # ---- 基本属性 -----------------------------------------------------
    @property
    def version(self):
        return self._version

    @property
    def readonly(self):
        return self._readonly

    def _writable(self):
        if self._readonly:
            raise ReadOnlyError(f"table {self.name!r} is read-only")

    # ---- 写入（推进版本，记日志） -------------------------------------
    def _next_id(self):
        return max(self._rows.keys(), default=0) + 1

    def insert(self, data, rid=None):
        self._writable()
        return self._apply_insert(data, rid)

    def _apply_insert(self, data, rid=None):
        rid = self._next_id() if rid is None else rid
        if rid in self._rows:
            raise CopyError(f"row id {rid} already exists in {self.name!r}")
        self._rows[rid] = _copy.deepcopy(data)
        self._version += 1
        self._changes.append({"version": self._version, "op": "insert",
                              "id": rid, "data": _copy.deepcopy(data)})
        return rid

    def update(self, rid, data):
        self._writable()
        if rid not in self._rows:
            raise CopyError(f"row id {rid} not found in {self.name!r}")
        self._rows[rid] = _copy.deepcopy(data)
        self._version += 1
        self._changes.append({"version": self._version, "op": "update",
                              "id": rid, "data": _copy.deepcopy(data)})

    def delete(self, rid):
        self._writable()
        if rid not in self._rows:
            raise CopyError(f"row id {rid} not found in {self.name!r}")
        del self._rows[rid]
        self._version += 1
        self._changes.append({"version": self._version, "op": "delete",
                              "id": rid})

    # ---- 快照与位点读 -------------------------------------------------
    def snapshot(self):
        """取一致性快照：位点 + 该位点的深拷贝数据。"""
        return Snapshot(self.name, self._version, _copy.deepcopy(self._rows))

    def changes_since(self, version):
        """返回 (version, 严格大于该位点] 的全部变更，按版本升序。"""
        return [_copy.deepcopy(c) for c in self._changes if c["version"] > version]

    def state_at(self, version):
        """重放日志还原任意历史位点的完整数据（快照可重复读的基础）。"""
        rows = {}
        for c in self._changes:
            if c["version"] > version:
                break
            if c["op"] in ("insert", "update"):
                rows[c["id"]] = _copy.deepcopy(c["data"])
            else:
                rows.pop(c["id"], None)
        return rows

    def rows(self):
        """当前数据的深拷贝（dict 输入兼容旧接口时也用得到）。"""
        return _copy.deepcopy(self._rows)

    def all_rows(self):
        """当前行数据列表，按 id 排序。"""
        return [_copy.deepcopy(self._rows[k])
                for k in sorted(self._rows.keys())]

    def checksum_at(self, version):
        return _checksum(self.state_at(version))


class ReadOnlyTable(Table):
    """只读表：任何写入抛 ``ReadOnlyError``，用于先在只读表上验证流程。"""

    def __init__(self, name, rows=()):
        super().__init__(name, rows, readonly=True)


class Snapshot:
    """一致性快照：位点（版本号）+ 该位点定住的数据。"""

    def __init__(self, name, version, rows):
        self.src_name = name
        self.position = version
        self.rows = rows

    def __repr__(self):
        return f"Snapshot(src={self.src_name!r}, position={self.position}, rows={len(self.rows)})"


class CopyResult(dict):
    """复制结果，属性方式和字典方式都能取（兼容旧测试的 dict 访问）。"""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


# ----------------------------------------------------------------------
# 模块级作业状态
# ----------------------------------------------------------------------
_jobs = {}        # dst -> 作业 dict（含检查点，半成品只在这里，对外不可查）
_published = {}   # dst -> 已发布目标表（完成或校验失败保留时才能 query）
_wrappers = {}    # dict 源表名 -> 包装出来的 Table，保证重跑拿到同一张表


def reset():
    """清空全部模块状态（测试隔离用）。"""
    _jobs.clear()
    _published.clear()
    _wrappers.clear()


def _as_table(src):
    if isinstance(src, Table):
        return src
    name = src.get("name", "")
    if name not in _wrappers:
        _wrappers[name] = ReadOnlyTable(name, src.get("rows", []))
    return _wrappers[name]


# 作业阶段：bulk（批量复制）/ catchup（增量补偿）/ verify_failed（校验不一致）
def copy(src, dst, *, batch_size=None, fail_at=None, tamper_after_publish=False):
    """复制 ``src`` 到 ``dst``，自动从上次断点续传。

    参数
    ----
    batch_size:
        批量复制每批行数；给定时在每批之间落检查点，用于演示断点续传。
    fail_at:
        故障注入（测试/演示用）。``("bulk", n)`` 在累计复制到第 n 行后失败；
        ``("catchup",)`` 在补偿阶段开始前失败。失败抛 :class:`CopyError`，
        半成品清理在暂存区、对外不可查，下次调用从检查点继续。
    tamper_after_publish:
        故障注入：发布后篡改目标表一行，模拟两侧不一致，验证校验拦截。
    """
    table = _as_table(src)
    job = _jobs.get(dst)
    resumed = job is not None

    if job is None:
        if dst in _published:
            raise CopyError(f"destination {dst!r} already exists and is published")
        snap = table.snapshot()
        job = {
            "src": table,
            "dst_name": dst,
            "snapshot_position": snap.position,
            "snapshot_rows": snap.rows,          # 定住的快照版本，重试不重读源表
            "target": Table(dst),                # 暂存目标，未发布前 query 不到
            "bulk_done": 0,
            "applied_position": snap.position,   # 已补偿到的变更位点
            "end_position": snap.position,
            "phase": "bulk",
            "fail_at": fail_at,
            "batch_size": batch_size,
        }
        _jobs[dst] = job
    elif job["phase"] == "verify_failed":
        raise CopyError(
            f"copy to {dst!r} stopped: verification failed, both sides retained; "
            "inspect verify()/query() or abort(dst) before retrying")

    try:
        _bulk_phase(job, fail_at)
        _catchup_phase(job, fail_at)
        published = _publish_phase(job)
        if tamper_after_publish:
            target = job["target"]
            first_id = next(iter(target.rows()))
            target.update(first_id, {"__tampered__": True})
        result = _verify_job(job, published, resumed)
        return result
    except CopyError:
        # 半成品保留在 _jobs 的暂存区并带检查点；未发布，query 看不到。
        raise


def _bulk_phase(job, fail_at):
    """分批复制快照数据，每批落检查点，重试只复制剩余部分。"""
    target = job["target"]
    ordered = sorted(job["snapshot_rows"].items())
    total = len(ordered)
    step = job.get("batch_size")
    if step is None and fail_at and fail_at[0] == "bulk":
        step = fail_at[1]  # 未指定批大小时，让故障注入仍有检查点可续
    while job["bulk_done"] < total:
        chunk = ordered[job["bulk_done"]: job["bulk_done"] + (step or total)]
        for rid, data in chunk:
            target.insert(data, rid=rid)
            job["bulk_done"] += 1
            if fail_at and fail_at[0] == "bulk" and job["bulk_done"] == fail_at[1]:
                job["phase"] = "bulk"
                raise CopyError(
                    f"injected failure during bulk copy at row {job['bulk_done']}")
    job["phase"] = "catchup"


def _catchup_phase(job, fail_at):
    """补偿复制期间的源表变更，幂等循环，直到追平时没有新变更。"""
    src = job["src"]
    target = job["target"]
    if fail_at and fail_at[0] == "catchup":
        job["fail_at"] = None  # 故障只注入一次，重试继续补偿
        job["phase"] = "catchup"
        raise CopyError("injected failure before applying incremental changes")

    guard = 0
    while True:
        changes = src.changes_since(job["applied_position"])
        if not changes:
            break
        for c in changes:
            if c["op"] == "insert":
                if c["id"] not in target.rows():
                    target.insert(c["data"], rid=c["id"])
            elif c["op"] == "update":
                target.update(c["id"], c["data"])
            else:  # delete
                if c["id"] in target.rows():
                    target.delete(c["id"])
            job["applied_position"] = c["version"]  # 逐条落检查点
        job["end_position"] = job["applied_position"]
        guard += 1
        if guard > 10_000:
            raise CopyError("catch-up did not converge (churn limit exceeded)")
    job["end_position"] = job["applied_position"]
    job["phase"] = "ready"


def _publish_phase(job):
    """原子发布：目标表进入可查询命名空间，此后可立即查询。"""
    _published[job["dst_name"]] = job["target"]
    job["phase"] = "published"
    return True


def _verify_job(job, published, resumed=False):
    """比较源/目标在补偿终点位点的行数与内容校验和。"""
    src = job["src"]
    target = job["target"]
    pos = job["end_position"]
    src_rows = src.state_at(pos)
    dst_rows = target.rows()
    src_count, dst_count = len(src_rows), len(dst_rows)
    src_sum, dst_sum = _checksum(src_rows), _checksum(dst_rows)
    diff = _diff_rows(src_rows, dst_rows)
    matched = not diff and src_count == dst_count and src_sum == dst_sum

    if not matched:
        # 停止并保留两侧数据：目标留在已发布命名空间，作业标记校验失败。
        job["phase"] = "verify_failed"

    return CopyResult({
        "src": src.name,
        "dst": job["dst_name"],
        "rows": dst_count,
        "snapshot": job["snapshot_position"],
        "snapshot_position": job["snapshot_position"],
        "end_position": pos,
        "resumed": resumed,
        "bulk_rows": job["bulk_done"],
        "caught_up": job["end_position"] - job["snapshot_position"],
        "src_count": src_count,
        "dst_count": dst_count,
        "src_checksum": src_sum,
        "dst_checksum": dst_sum,
        "checksum": dst_sum,
        "matched": matched,
        "diff": diff,
        "published": published,
        "status": job["phase"],
    })


def _diff_rows(src_rows, dst_rows):
    """逐行内容差异：缺失 / 多余 / 内容不一致。"""
    diff = []
    for rid in sorted(set(src_rows) | set(dst_rows)):
        if rid not in dst_rows:
            diff.append({"id": rid, "kind": "missing_in_dst", "src": src_rows[rid]})
        elif rid not in src_rows:
            diff.append({"id": rid, "kind": "extra_in_dst", "dst": dst_rows[rid]})
        elif _canonical(src_rows[rid]) != _canonical(dst_rows[rid]):
            diff.append({"id": rid, "kind": "content_mismatch",
                         "src": src_rows[rid], "dst": dst_rows[rid]})
    return diff


def verify(dst=None):
    """校验已发布的复制结果。

    不传 ``dst`` 时汇总全部已发布目标，整体 matched 为所有作业的与；
    传入时返回该作业的完整差异、两侧计数与校验和。快照位点可查询。
    """
    if dst is not None:
        if dst not in _published:
            raise CopyError(f"no published table named {dst!r}")
        return _verify_job(_jobs[dst], published=True)

    results = [_verify_job(job, published=True)
               for name, job in _jobs.items() if name in _published]
    total_src = sum(r["src_count"] for r in results)
    total_dst = sum(r["dst_count"] for r in results)
    h = hashlib.sha256()
    for r in results:
        h.update(r["dst_checksum"].encode("utf-8"))
    return {
        "rows": total_dst,
        "src_count": total_src,
        "dst_count": total_dst,
        "matched": all(r["matched"] for r in results) if results else True,
        "checksum": h.hexdigest(),
        "jobs": {r["dst"]: {
            "snapshot_position": r["snapshot_position"],
            "end_position": r["end_position"],
            "matched": r["matched"],
            "diff": r["diff"],
        } for r in results},
    }


def query(dst):
    """查询已发布目标表；未发布（半成品）或不存在时抛错。"""
    if dst not in _published:
        raise CopyError(f"destination {dst!r} is not published (half-finished copies are not queryable)")
    return _published[dst].all_rows()


def abort(dst):
    """放弃复制：清理半成品（暂存区 + 已发布目标 + 检查点）。"""
    existed = dst in _jobs or dst in _published
    _jobs.pop(dst, None)
    _published.pop(dst, None)
    return existed


def snapshot_of(dst):
    """查询某次复制的快照位点（未发布/已发布均可查）。"""
    if dst not in _jobs:
        raise CopyError(f"no copy job named {dst!r}")
    job = _jobs[dst]
    return {"dst": dst, "snapshot_position": job["snapshot_position"],
            "applied_position": job["applied_position"],
            "end_position": job["end_position"], "phase": job["phase"]}
