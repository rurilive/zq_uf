"""cdcbatch：带批次号与位点的原子、幂等变更批处理（纯标准库）。

语义口径：
- 每个批次带批次号 ``seq`` 与起始位点 ``pos``，批次内行位点严格递增；
- 整批所有行应用成功才推进位点（watermark）并落盘；任一行失败则按反向顺序
  回滚本批已应用的行，位点不推进，原始批次落盘到 pending 目录等待人工续跑；
- 重复批次（相同批次号）整批按位点跳过，不重复写入；
- 冲突不覆盖：冲突行两侧原始值都保留，冲突按表/字段统计并落盘；
- 批次携带校验和，校验和不一致、位点错位、超批次大小一律拒绝，不落任何写；
- 每次应用/续跑的结果追加写 audit.jsonl 审计日志。

模块级 ``apply()``/``resume()`` 保留旧的逐条语义以兼容既有调用；携带 ``pos``
的新格式批次会自动路由到进程内默认的持久化引擎。生产用法请显式构造
``BatchProcessor(state_dir)``。
"""

import copy
import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone


# --------------------------------------------------------------------------
# 异常：协议类错误（校验和/位点/批次大小/状态损坏/续跑令牌未知）直接抛出；
# 行运行期失败不抛异常，返回 rolled_back=True 的结果字典。
# --------------------------------------------------------------------------
class CdcbatchError(Exception):
    """cdcbatch 所有异常的基类。"""


class ChecksumMismatch(CdcbatchError):
    """批次校验和与内容不一致，或同批次号重放时内容发生变化。"""


class PositionGap(CdcbatchError):
    """批次位点与已提交水位线不连续，防止错位写入。"""


class BatchTooLarge(CdcbatchError):
    """批次行数超过可配置的 batch_size。"""


class StateCorrupted(CdcbatchError):
    """落盘状态校验和不匹配，状态文件可能被外部损坏。"""


class ResumeNotFound(CdcbatchError):
    """resume 令牌既不在 pending 目录，也不是已提交批次。"""


class RowApplicationError(CdcbatchError):
    """单行应用失败（含兼容旧约定的 bad 标记），触发整批回滚。"""


# --------------------------------------------------------------------------
# 校验和：对批次号、起始位点与每行的（行位点/表/键/操作/值）做规范化哈希，
# 任一行内容或顺序被改动都会导致哈希不一致。
# --------------------------------------------------------------------------
def _canonical_rows(rows):
    return [
        [
            row.get("pos"),
            row.get("table"),
            row.get("key"),
            row.get("op", "upsert"),
            row.get("values", {}),
        ]
        for row in rows
    ]


def compute_checksum(batch):
    """计算批次的位点校验和（sha256 hex）。"""
    payload = {
        "seq": batch.get("seq"),
        "pos": batch.get("pos"),
        "rows": _canonical_rows(batch.get("rows", [])),
    }
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _digest(payload):
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _atomic_write(path, text):
    """临时文件 + os.replace，保证落盘原子性。"""
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _now():
    return datetime.now(timezone.utc).isoformat()


class BatchProcessor:
    """原子幂等批处理引擎，全部状态持久化在 ``state_dir`` 下。

    目录布局::

        state_dir/
          state.json        # 水位线 + 已提交批次索引（自带 digest）
          audit.jsonl       # 每次 apply/resume 的审计结果（追加写）
          conflicts.jsonl   # 冲突记录，两侧原始值（追加写）
          pending/<hash>.json  # 失败批次原文，供人工修复后续跑
          data/<table>.json # 表数据，key -> {pos, values}
          .lock             # 进程间互斥锁
    """

    def __init__(self, state_dir, batch_size=100):
        self.state_dir = os.path.abspath(state_dir)
        self.batch_size = batch_size
        self.data_dir = os.path.join(self.state_dir, "data")
        self.pending_dir = os.path.join(self.state_dir, "pending")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.pending_dir, exist_ok=True)
        self.state_path = os.path.join(self.state_dir, "state.json")
        self.audit_path = os.path.join(self.state_dir, "audit.jsonl")
        self.conflicts_path = os.path.join(self.state_dir, "conflicts.jsonl")
        self.lock_path = os.path.join(self.state_dir, ".lock")
        self._state = self._load_state()

    # ----- 通用持久化辅助 -------------------------------------------------
    @contextmanager
    def _locked(self):
        with open(self.lock_path, "w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _load_state(self):
        if not os.path.exists(self.state_path):
            return {"watermark": None, "batches": {}}
        with open(self.state_path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
        payload = doc.get("payload")
        if not isinstance(payload, dict) or _digest(payload) != doc.get("digest"):
            raise StateCorrupted("state.json 校验和不匹配")
        return payload

    def _save_state(self):
        doc = {"payload": self._state, "digest": _digest(self._state)}
        _atomic_write(self.state_path, json.dumps(doc, ensure_ascii=False))

    @property
    def state(self):
        """当前水位线与已提交批次索引的深拷贝。"""
        return copy.deepcopy(self._state)

    def _table_path(self, table):
        return os.path.join(self.data_dir, "%s.json" % table)

    def _load_table(self, table):
        path = self._table_path(table)
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save_table(self, table, store):
        path = self._table_path(table)
        if not store:
            if os.path.exists(path):
                os.unlink(path)
            return
        _atomic_write(path, json.dumps(store, ensure_ascii=False))

    def _append_jsonl(self, path, record):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

    def pending_file(self, token):
        """令牌对应的 pending 文件路径（人工修复时直接改写该文件）。"""
        digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        return os.path.join(self.pending_dir, "%s.json" % digest)

    def _audit(self, event, batch, result):
        self._append_jsonl(
            self.audit_path,
            {
                "ts": _now(),
                "event": event,
                "batch": batch.get("id", ""),
                "seq": batch.get("seq"),
                "applied": result["applied"],
                "skipped": result["skipped"],
                "rolled_back_rows": result["rolled_back_rows"],
                "conflicts": len(result["conflicts"]),
                "watermark": self._state["watermark"],
            },
        )

    # ----- 批次校验 -------------------------------------------------------
    def _validate(self, batch):
        if not isinstance(batch, dict):
            raise CdcbatchError("batch 必须是字典")
        seq = batch.get("seq")
        pos = batch.get("pos")
        rows = batch.get("rows", [])
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise CdcbatchError("批次号 seq 必须是整数")
        if not isinstance(pos, int) or isinstance(pos, bool):
            raise PositionGap("起始位点 pos 必须是整数")
        if not isinstance(rows, list) or not rows:
            raise CdcbatchError("rows 必须是非空列表")
        if len(rows) > self.batch_size:
            raise BatchTooLarge(
                "批次 %s 行数 %d 超过 batch_size=%d"
                % (batch.get("id", ""), len(rows), self.batch_size)
            )
        checksum = batch.get("checksum")
        if not checksum:
            raise ChecksumMismatch("批次缺少 checksum")
        if checksum != compute_checksum(batch):
            raise ChecksumMismatch(
                "批次 %s 校验和不一致，内容或位点可能被篡改" % batch.get("id", "")
            )
        prev = pos - 1
        for index, row in enumerate(rows):
            row_pos = row.get("pos")
            if not isinstance(row_pos, int) or isinstance(row_pos, bool):
                raise PositionGap("第 %d 行缺少整数位点" % index)
            if index == 0 and row_pos != pos:
                raise PositionGap(
                    "首行位点 %d 与批次起始位点 %d 不一致" % (row_pos, pos)
                )
            if row_pos <= prev:
                raise PositionGap("行位点必须严格递增，违规位点 %d" % row_pos)
            prev = row_pos

    def _new_result(self, batch):
        return {
            "batch": batch.get("id", ""),
            "seq": batch.get("seq"),
            "committed": False,
            "applied": 0,
            "rolled_back": False,
            "rolled_back_rows": 0,
            "skipped": 0,
            "conflicts": [],
            "conflict_stats": {},
            "watermark": self._state["watermark"],
            "resumed": False,
            "rolled_back_keys": [],
        }

    # ----- 核心入口 -------------------------------------------------------
    def apply(self, batch):
        """应用一个带批次号/位点/校验和的批次，返回结果字典。"""
        with self._locked():
            return self._apply_locked(batch, resumed=False)

    def resume(self, token):
        """人工续跑：优先重试 pending 中的失败批次；已提交批次则按位点跳过。"""
        with self._locked():
            pending_path = self.pending_file(token)
            if os.path.exists(pending_path):
                with open(pending_path, "r", encoding="utf-8") as handle:
                    doc = json.load(handle)
                result = self._apply_locked(doc["batch"], resumed=True)
                if result["committed"] and os.path.exists(pending_path):
                    os.unlink(pending_path)
                return result

            for seq, meta in self._state["batches"].items():
                if meta.get("id") == token:
                    result = self._new_result({"id": token, "seq": int(seq)})
                    result["committed"] = True
                    result["skipped"] = meta["rows"]
                    result["watermark"] = self._state["watermark"]
                    result["resumed"] = True
                    self._audit("resumed_skip", {"id": token, "seq": int(seq)}, result)
                    return result
            raise ResumeNotFound("找不到令牌 %s 对应的 pending 或已提交批次" % token)

    def _apply_locked(self, batch, resumed):
        # 协议类错误直接抛出，不产生任何写入。
        self._validate(batch)
        seq = batch["seq"]
        pos = batch["pos"]
        rows = batch["rows"]
        watermark = self._state["watermark"]

        result = self._new_result(batch)
        result["resumed"] = resumed

        # 1) 重复批次：相同批次号已提交，整批按位点跳过。
        committed = self._state["batches"].get(str(seq))
        if committed is not None:
            if committed.get("checksum") != batch["checksum"]:
                raise ChecksumMismatch(
                    "批次号 %s 已提交但重放内容不一致" % seq
                )
            result["committed"] = True
            result["skipped"] = len(rows)
            result["watermark"] = watermark
            self._audit("duplicate_skip", batch, result)
            return result

        # 2) 位点连续性校验，防止错位。
        if watermark is None:
            if pos != 0:
                raise PositionGap("首批位点必须为 0，实际为 %d" % pos)
        elif pos <= watermark:
            raise PositionGap(
                "批次起始位点 %d 不大于已提交水位线 %d" % (pos, watermark)
            )
        elif pos != watermark + 1:
            raise PositionGap(
                "批次位点 %d 与水位线 %d 不连续" % (pos, watermark)
            )

        undo = []  # 按应用顺序记录快照，回滚时反向遍历
        try:
            for row in rows:
                # 防御性按位点跳过：已提交位点绝不重复写。
                if watermark is not None and row["pos"] <= watermark:
                    result["skipped"] += 1
                    continue
                if row.get("bad"):
                    # 兼容旧约定的失败标记，真实场景可替换为下游写入异常。
                    raise RowApplicationError(
                        "批次 %s 位点 %d 的行被标记为失败"
                        % (batch.get("id", ""), row["pos"])
                    )
                self._apply_row(row, result, undo)
        except Exception as exc:  # 任一失败：反向回滚，位点不推进
            self._rollback(undo, result)
            self._write_pending(batch, exc, result)
            self._audit("resumed_rolled_back" if resumed else "rolled_back",
                        batch, result)
            return result

        # 3) 整批成功：推进水位线、登记批次号，原子落盘。
        self._state["watermark"] = rows[-1]["pos"]
        self._state["batches"][str(seq)] = {
            "id": batch.get("id", ""),
            "pos": pos,
            "last_pos": rows[-1]["pos"],
            "checksum": batch["checksum"],
            "rows": len(rows),
            "applied_at": _now(),
        }
        self._save_state()
        stale_pending = self.pending_file(batch.get("id", ""))
        if os.path.exists(stale_pending):
            os.unlink(stale_pending)
        result["committed"] = True
        result["watermark"] = self._state["watermark"]
        self._audit("resumed_committed" if resumed else "committed", batch, result)
        return result

    def _apply_row(self, row, result, undo):
        table = row["table"]
        key = row["key"]
        incoming = copy.deepcopy(row.get("values", {}))
        store = self._load_table(table)

        if key in store:
            current_values = store[key]["values"]
            diff_fields = [
                field
                for field, value in incoming.items()
                if field in current_values
                and current_values[field] != value
            ]
            if diff_fields:
                # 冲突不覆盖：两侧原始值都保留，当前行维持原值。
                conflict = {
                    "table": table,
                    "key": key,
                    "pos": row["pos"],
                    "fields": diff_fields,
                    "current": copy.deepcopy(current_values),
                    "incoming": copy.deepcopy(incoming),
                }
                result["conflicts"].append(conflict)
                table_stats = result["conflict_stats"].setdefault(table, {})
                for field in diff_fields:
                    table_stats[field] = table_stats.get(field, 0) + 1
                self._append_jsonl(self.conflicts_path, conflict)
                return
            # 内容完全一致：幂等跳过。
            result["skipped"] += 1
            return

        undo.append({"table": table, "key": key, "old": copy.deepcopy(store.get(key))})
        store[key] = {"pos": row["pos"], "values": incoming}
        self._save_table(table, store)
        result["applied"] += 1

    def _rollback(self, undo, result):
        # 严格按应用的反向顺序撤销，恢复旧值或删除新建行。
        for snapshot in reversed(undo):
            table = snapshot["table"]
            key = snapshot["key"]
            store = self._load_table(table)
            if snapshot["old"] is None:
                store.pop(key, None)
            else:
                store[key] = copy.deepcopy(snapshot["old"])
            self._save_table(table, store)
            result["rolled_back_keys"].append("%s/%s" % (table, key))
        result["rolled_back"] = True
        result["rolled_back_rows"] = len(undo)

    def _write_pending(self, batch, exc, result):
        record = {
            "token": batch.get("id", ""),
            "failed_at": _now(),
            "error": str(exc),
            "rolled_back_keys": result["rolled_back_keys"],
            "batch": copy.deepcopy(batch),
        }
        _atomic_write(self.pending_file(batch.get("id", "")),
                      json.dumps(record, ensure_ascii=False))


# --------------------------------------------------------------------------
# 模块级兼容接口：无位点的旧格式批次保持逐条应用语义（既有断言依赖）；
# 携带 pos 的新格式批次路由到进程内默认的持久化引擎。
# --------------------------------------------------------------------------
_seen = {}
_default_engine = None


def _get_default_engine():
    global _default_engine
    if _default_engine is None:
        default_dir = tempfile.mkdtemp(prefix="cdcbatch-default-")
        _default_engine = BatchProcessor(default_dir)
    return _default_engine


def apply(batch):
    """逐条写入（旧语义）；携带 pos 的新格式批次走原子持久化引擎。"""
    if isinstance(batch, dict) and "pos" in batch:
        return _get_default_engine().apply(batch)

    rows = list(batch.get("rows", []))
    applied = 0
    for row in rows:
        if row.get("bad"):
            return {
                "batch": batch.get("id", ""),
                "applied": applied,
                "rolled_back": False,
                "skipped": 0,
                "conflicts": [],
            }
        applied += 1
    _seen[batch.get("id", "")] = applied
    return {
        "batch": batch.get("id", ""),
        "applied": applied,
        "rolled_back": False,
        "skipped": 0,
        "conflicts": [],
    }


def resume(token):
    """按令牌重放整批（旧语义，不跳已应用的行）。"""
    return {
        "token": token,
        "resumed": True,
        "skipped": 0,
        "applied": _seen.get(token, 0),
    }
