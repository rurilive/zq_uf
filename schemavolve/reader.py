"""按固定schema读取JSONL数据。"""
import json


class SchemaMismatch(ValueError):
    """文件字段和schema不完全一致。"""


def read(path, schema):
    """要求每行列名和schema完全一致，不做补齐和类型转换。"""
    expected = list(schema)
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            missing = [name for name in expected if name not in raw]
            extra = [name for name in raw if name not in expected]
            if missing or extra:
                raise SchemaMismatch(
                    "line %d: missing=%s extra=%s" % (line_no, missing, extra)
                )
            rows.append({name: raw[name] for name in expected})
    return rows
