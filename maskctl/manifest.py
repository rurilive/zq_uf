"""处理清单：按 (表, 字段) 记录处理情况，可按表导出 CSV。

下游可据此看出每个字段是否被处理过、用的是规则还是默认策略、
以及处理时的规则版本和密钥版本。
"""
import csv
import io


class Manifest:
    def __init__(self):
        self._records = {}  # (table, field) -> record

    def note(self, table, field, source, rule_version, key_version):
        key = (table, field)
        rec = self._records.get(key)
        if rec is None:
            rec = self._records[key] = {
                "table": table,
                "field": field,
                "source": source,
                "rule_version": rule_version,
                "key_version": key_version,
                "rows": 0,
            }
        rec["rows"] += 1
        rec["rule_version"] = rule_version
        rec["key_version"] = key_version

    def for_table(self, table):
        return [
            dict(r)
            for t, r in sorted(self._records.items(), key=lambda kv: kv[0])
            if t[0] == table
        ]

    def export(self, table):
        """按表导出处理清单 CSV。"""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            ["table", "field", "source", "rule_version", "key_version", "rows"]
        )
        for r in self.for_table(table):
            w.writerow(
                [
                    r["table"],
                    r["field"],
                    r["source"],
                    r["rule_version"],
                    r["key_version"],
                    r["rows"],
                ]
            )
        return buf.getvalue()
