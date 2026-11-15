"""出队记录：按租户/站点留存，支持按天查询、导出与回放。"""

import csv
import io
import json

_CSV_COLUMNS = [
    "dequeued_at",
    "day",
    "tenant",
    "site",
    "id",
    "priority",
    "boost",
    "effective_priority",
    "waited_seconds",
    "attempt",
]


class RecordLog:
    """内存出队日志，结构为 tenant -> site -> 记录列表。"""

    def __init__(self):
        self._rows = {}

    def add(self, tenant, site, record):
        self._rows.setdefault(tenant, {}).setdefault(site, []).append(dict(record))

    def query(self, tenant, site=None, day=None):
        """按站点、按天过滤；不传条件则返回该租户全部记录，按出队时间排序。"""
        sites = [site] if site is not None else sorted(self._rows.get(tenant, {}))
        out = []
        for s in sites:
            for row in self._rows.get(tenant, {}).get(s, []):
                if day is None or row["day"] == day:
                    out.append(dict(row))
        out.sort(key=lambda r: (r["dequeued_at"], r["site"], r["id"]))
        return out

    def export(self, tenant, site=None, day=None, fmt="json"):
        """导出为 json 字符串、csv 字符串或 dict 记录列表。"""
        rows = self.query(tenant, site, day)
        if fmt in ("dict", "records"):
            return rows
        if fmt == "json":
            return json.dumps(rows, ensure_ascii=False, indent=2)
        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return buf.getvalue()
        raise ValueError(f"不支持的导出格式: {fmt}")

    def snapshots_for_replay(self, tenant, day, site=None):
        """取某天的出队任务快照，供回放重新入队。"""
        return self.query(tenant, site, day)
