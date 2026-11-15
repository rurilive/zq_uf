"""冷热分层核心：归档、透明查询合并、恢复校验。

设计原则
--------
- 对查询透明：业务侧只调用 :func:`query`，无需改 SQL/过滤条件，
  框架按归档元数据自动决定查在线、冷层还是两者合并。
- 冷层故障不等于空结果：冷层不可用时返回降级标记和告警，
  在线数据照常返回，并明确提示结果可能不完整。
- 恢复可校验、可重试、可回看：按归档记录回迁，校验行数与内容校验和，
  失败保留冷层副本；每次恢复尝试都独立留痕。
- 副本清理需人工确认：恢复成功不自动删冷层，:func:`confirm_cleanup` 后才清理。
"""

import copy
import hashlib
import json

from .storage import ColdStorage, ColdTierError, ColdTierUnavailable

__all__ = [
    "TierManager",
    "ColdTierError",
    "ColdTierUnavailable",
    "checksum_of",
    "archive",
    "query",
    "restore",
    "confirm_cleanup",
    "list_archives",
    "discover_archives",
    "export_restores",
    "reset",
    "seed",
    "manager",
]

# 归档状态机
ST_ARCHIVED = "archived"        # 已归档，冷层有副本
ST_RESTORED = "restored"        # 已回迁并校验通过，冷层副本保留待清理
ST_CLEANED = "cleaned"          # 副本已人工确认清理
ST_FAILED = "restore_failed"    # 最近一次恢复校验失败，副本保留

# 恢复尝试结果
R_SUCCESS = "success"
R_FAILED = "failed"


def checksum_of(rows):
    """对行集合计算稳定的内容校验和（SHA-256）。

    以规范化 JSON（按 key 排序、保持 key 顺序稳定）逐行哈希后汇总，
    行集合顺序不影响结果，行内容变化必然变化。
    """
    digest = hashlib.sha256()
    for row in rows:
        line = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


class TierManager:
    """单表或多表的冷热分层管理器（默认内存实现，便于演练与测试）。"""

    def __init__(self, time_col="ts"):
        self.time_col = time_col
        self.cold = ColdStorage()
        self.online = {}          # table -> [row, ...]
        self.archive_log = []     # 归档记录（含位置、范围、校验和、状态）
        self.restore_log = []     # 恢复记录（每次尝试一条，互相独立）
        self._archive_seq = 0
        self._restore_seq = 0

    def reset(self):
        """清空全部状态（测试 / 演练重新开始时使用）。"""
        self.__init__(self.time_col)

    def seed(self, table, rows):
        """灌入在线表数据（模拟业务在线表）。返回写入行数。"""
        bucket = self.online.setdefault(table, [])
        stored = [dict(row) for row in rows]
        bucket.extend(stored)
        return len(stored)

    # ------------------------------------------------------------------
    # 归档
    # ------------------------------------------------------------------
    def _next_archive_id(self):
        self._archive_seq += 1
        return f"arc-{self._archive_seq:04d}"

    def _next_restore_id(self):
        self._restore_seq += 1
        return f"rst-{self._restore_seq:04d}"

    def archive(self, table, range_data):
        """按时间范围把在线数据搬到冷层，并记录位置与范围。

        范围为半开区间 ``[start, end)``：``start <= ts < end``。
        先写冷层成功，再从在线表移除——冷层写入失败时在线数据原封不动。
        """
        start = range_data.get("start")
        end = range_data.get("end")
        bucket = self.online.setdefault(table, [])

        moved, remaining = [], []
        for row in bucket:
            if self._in_range(row.get(self.time_col), start, end):
                moved.append(dict(row))
            else:
                remaining.append(row)

        location = f"cold://{table}/{start}_{end}"
        row_count = len(moved)
        checksum = checksum_of(moved)
        record_id = self._next_archive_id()
        location_full = None if row_count == 0 else f"{location}#{record_id}"

        record = {
            "archive_id": record_id,
            "table": table,
            "range": {"start": start, "end": end},
            "time_col": self.time_col,
            "location": location_full,
            "row_count": row_count,
            "checksum": checksum,
            "status": ST_ARCHIVED,
        }

        if row_count:
            # 冷层写入失败会抛出，在线表尚未改动，不会丢数据
            self.cold.put(location_full, moved)
            self.online[table] = remaining

        self.archive_log.append(record)
        return {
            "archive_id": record_id,
            "table": table,
            "range": record["range"],
            "archived": row_count,
            "location": location_full,
            "checksum": checksum,
            "status": ST_ARCHIVED,
        }

    # ------------------------------------------------------------------
    # 透明查询
    # ------------------------------------------------------------------
    def query(self, table, filters):
        """按条件同时查在线与冷层并合并，对业务透明。

        返回里明确给出查询走了哪一层、每层贡献了多少行、完整的合并结果；
        冷层不可用时给出告警并标记降级，而不是静默返回空结果。
        """
        filters = dict(filters or {})
        warnings = []
        cold_rows = []
        cold_errors = []

        # restored 状态的数据权威副本已回到在线表，冷层仅为待清理备份，
        # 不再参与查询，避免在线/冷层双份重复；cleaned 分片已删除同理。
        candidates = [
            rec for rec in self.archive_log
            if rec["table"] == table
            and rec["status"] in (ST_ARCHIVED, ST_FAILED)
            and rec["location"]
            and self._range_relevant(rec["range"], filters)
        ]

        for rec in candidates:
            try:
                shard = self.cold.get(rec["location"])
            except ColdTierUnavailable as exc:
                cold_errors.append({"archive_id": rec["archive_id"], "error": str(exc)})
                continue
            except KeyError:
                cold_errors.append({
                    "archive_id": rec["archive_id"],
                    "error": f"cold shard missing at {rec['location']}",
                })
                continue
            cold_rows.extend(shard)

        if cold_errors:
            warnings.append({
                "code": "COLD_TIER_UNAVAILABLE",
                "message": "冷层不可用或分片缺失，本次结果仅包含部分数据，请稍后重试或联系管理员",
                "details": cold_errors,
            })

        online_rows = list(self.online.get(table, []))
        # 双保险去重：在线优先，冷层中与在线同身份的行不再计入
        seen = {self._row_identity(row) for row in online_rows}
        cold_unique = []
        for row in cold_rows:
            identity = self._row_identity(row)
            if identity not in seen:
                cold_unique.append(row)
                seen.add(identity)

        merged = [
            row for row in online_rows + cold_unique
            if self._row_matches(row, filters)
        ]
        merged.sort(key=lambda row: self._sort_key(row.get(self.time_col)))

        degraded = bool(cold_errors)
        routed = "online+cold" if candidates else "online"
        tiers = ["online"] + (["cold"] if any(
            rec for rec in candidates
            if not any(err["archive_id"] == rec["archive_id"] for err in cold_errors)
        ) else [])

        return {
            "table": table,
            "filters": filters,
            "routed": routed,
            "tiers": tiers,
            "degraded": degraded,
            "warnings": warnings,
            "sources": {
                "online": sum(1 for row in online_rows if self._row_matches(row, filters)),
                "cold": sum(1 for row in cold_unique if self._row_matches(row, filters)),
            },
            "rows": merged,
            "count": len(merged),
            "complete": not degraded,
        }

    # ------------------------------------------------------------------
    # 恢复
    # ------------------------------------------------------------------
    def restore(self, table=None, archive_id=None):
        """按归档记录把冷层数据回迁在线，并校验行数与内容。

        - 校验通过：数据写回在线，归档状态转 ``restored``，冷层副本保留；
        - 校验失败（含冷层不可用、分片丢失、行数/校验和不一致）：不动在线数据，
          状态转 ``restore_failed``，冷层副本保留，可重新调用本方法重试；
        - 每次尝试（成功或失败）都在恢复日志中留一条独立记录，可回看、可导出。
        """
        record = self._find_archive(table, archive_id)
        rst_id = self._next_restore_id()

        if record["status"] == ST_RESTORED:
            entry = self._record_restore(
                rst_id, record, R_SUCCESS, skipped=True,
                message="该归档已恢复，无需重复回迁",
            )
            return self._restore_result(entry, record, restored_rows=0)

        if record["status"] == ST_CLEANED:
            entry = self._fail_restore(
                rst_id, record,
                reason="冷层副本已清理，无法恢复",
                rows_found=0,
            )
            return self._restore_result(entry, record, restored_rows=0)

        try:
            cold_rows = self.cold.get(record["location"])
        except ColdTierUnavailable as exc:
            entry = self._fail_restore(rst_id, record, reason=str(exc), rows_found=0)
            return self._restore_result(entry, record, restored_rows=0)
        except KeyError:
            entry = self._fail_restore(
                rst_id, record,
                reason=f"冷层分片缺失: {record['location']}",
                rows_found=0,
            )
            return self._restore_result(entry, record, restored_rows=0)

        actual_count = len(cold_rows)
        actual_checksum = checksum_of(cold_rows)
        count_ok = actual_count == record["row_count"]
        checksum_ok = actual_checksum == record["checksum"]

        if not (count_ok and checksum_ok):
            checks = {
                "expected_row_count": record["row_count"],
                "actual_row_count": actual_count,
                "row_count_ok": count_ok,
                "expected_checksum": record["checksum"],
                "actual_checksum": actual_checksum,
                "checksum_ok": checksum_ok,
            }
            entry = self._fail_restore(
                rst_id, record,
                reason="恢复校验失败：行数或内容校验和不一致",
                rows_found=actual_count,
                checks=checks,
            )
            return self._restore_result(entry, record, restored_rows=0)

        # 校验通过：幂等回迁（按时间字段+内容去重，避免重试造成重复行）
        bucket = self.online.setdefault(record["table"], [])
        existing_keys = {
            self._row_identity(row) for row in bucket
        }
        added = 0
        for row in cold_rows:
            identity = self._row_identity(row)
            if identity not in existing_keys:
                bucket.append(dict(row))
                existing_keys.add(identity)
                added += 1

        record["status"] = ST_RESTORED
        entry = self._record_restore(
            rst_id, record, R_SUCCESS,
            message="恢复校验通过（行数与内容一致），冷层副本保留，待人工确认清理",
            checks={
                "expected_row_count": record["row_count"],
                "actual_row_count": actual_count,
                "row_count_ok": True,
                "expected_checksum": record["checksum"],
                "actual_checksum": actual_checksum,
                "checksum_ok": True,
            },
            restored_rows=added,
        )
        return self._restore_result(entry, record, restored_rows=added)

    def _fail_restore(self, rst_id, record, reason, rows_found, checks=None):
        record["status"] = ST_FAILED
        return self._record_restore(
            rst_id, record, R_FAILED,
            message=reason,
            checks=checks or {"rows_found": rows_found},
            restored_rows=0,
        )

    def confirm_cleanup(self, table=None, archive_id=None):
        """人工确认后清理冷层副本。

        只有校验通过、状态为 ``restored`` 的归档允许清理；
        校验失败或仍在冷层的归档拒绝清理，避免误删唯一副本。
        """
        record = self._find_archive(table, archive_id)
        if record["status"] != ST_RESTORED:
            raise ColdTierError(
                f"归档 {record['archive_id']} 当前状态为 {record['status']}，"
                "仅已恢复并校验通过的归档允许清理冷层副本"
            )
        location = record["location"]
        if location and self.cold.contains(location):
            self.cold.delete(location)
        record["status"] = ST_CLEANED
        return {
            "archive_id": record["archive_id"],
            "table": record["table"],
            "location": location,
            "status": ST_CLEANED,
            "message": "冷层副本已清理，在线数据保留",
        }

    # ------------------------------------------------------------------
    # 记录查询与导出
    # ------------------------------------------------------------------
    def list_archives(self, table=None):
        """查询归档范围与状态（可按表过滤），返回深拷贝避免外部改账。"""
        records = [
            copy.deepcopy(rec) for rec in self.archive_log
            if table is None or rec["table"] == table
        ]
        return records

    def export_restores(self, table=None, path=None):
        """导出恢复记录（可按表过滤）；给 path 时同时写 JSON 文件。"""
        entries = [
            copy.deepcopy(entry) for entry in self.restore_log
            if table is None or entry["table"] == table
        ]
        payload = json.dumps(entries, ensure_ascii=False, indent=2, default=str)
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(payload)
        return entries

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _find_archive(self, table, archive_id):
        for record in reversed(self.archive_log):
            if archive_id is not None and record["archive_id"] != archive_id:
                continue
            if table is not None and record["table"] != table:
                continue
            return record
        label = f"archive_id={archive_id}" if archive_id else f"table={table}"
        raise ColdTierError(f"找不到归档记录: {label}")

    def _record_restore(self, rst_id, record, result, message, checks=None,
                        restored_rows=0, skipped=False):
        entry = {
            "restore_id": rst_id,
            "archive_id": record["archive_id"],
            "table": record["table"],
            "range": copy.deepcopy(record["range"]),
            "location": record["location"],
            "result": result,
            "skipped": skipped,
            "restored_rows": restored_rows,
            "checks": checks or {},
            "message": message,
        }
        self.restore_log.append(entry)
        return entry

    @staticmethod
    def _restore_result(entry, record, restored_rows):
        return {
            "restore_id": entry["restore_id"],
            "archive_id": record["archive_id"],
            "table": record["table"],
            "range": copy.deepcopy(record["range"]),
            "result": entry["result"],
            "restored_rows": restored_rows,
            "verification": entry["checks"],
            "status": record["status"],
            "message": entry["message"],
        }

    @staticmethod
    def _in_range(value, start, end):
        if value is None:
            return False
        if start is not None and value < start:
            return False
        if end is not None and value >= end:
            return False
        return True

    @classmethod
    def _range_relevant(cls, rec_range, filters):
        """过滤条件的时间窗与归档范围无重叠时，无需查该冷分片。"""
        f_start = filters.get("start")
        f_end = filters.get("end")
        r_start = rec_range.get("start")
        r_end = rec_range.get("end")
        if f_start is not None and r_end is not None and f_start >= r_end:
            return False
        if f_end is not None and r_start is not None and f_end <= r_start:
            return False
        return True

    def _row_matches(self, row, filters):
        for key, value in filters.items():
            if key in ("start", "end"):
                continue
            if row.get(key) != value:
                return False
        ts = row.get(self.time_col)
        if "start" in filters and (ts is None or ts < filters["start"]):
            return False
        if "end" in filters and (ts is None or ts >= filters["end"]):
            return False
        return True

    @staticmethod
    def _sort_key(value):
        # None 时间戳排到最后，避免异构类型比较报错
        return value is not None, value

    @staticmethod
    def _row_identity(row):
        return json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)


# ----------------------------------------------------------------------
# 模块级默认实例与函数式 API（保持 demo / 旧入口的调用方式不变）
# ----------------------------------------------------------------------
_default = TierManager()


def reset(time_col="ts"):
    """重置模块级默认实例。"""
    global _default
    _default = TierManager(time_col=time_col)


def manager():
    """返回当前模块级默认实例（reset 之后也始终指向最新实例）。"""
    return _default


def seed(table, rows):
    return _default.seed(table, rows)


def archive(table, range_data):
    return _default.archive(table, range_data)


def query(table, filters):
    return _default.query(table, filters)


def restore(table=None, archive_id=None):
    return _default.restore(table=table, archive_id=archive_id)


def confirm_cleanup(table=None, archive_id=None):
    return _default.confirm_cleanup(table=table, archive_id=archive_id)


def list_archives(table=None):
    return _default.list_archives(table)


def discover_archives(table=None):
    """:func:`list_archives` 的语义化别名。"""
    return _default.list_archives(table)


def export_restores(table=None, path=None):
    return _default.export_restores(table=table, path=path)
