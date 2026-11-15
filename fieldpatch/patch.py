"""行级字段修补（无状态纯函数）与批量编排入口。

``patch`` 只处理单行、不碰全局状态，便于单测与在单站点试运行；
批次、统计、回滚、回放、告警、导出由 :mod:`fieldpatch.store` 承担。
"""

from .schema import MISSING, normalize_schema, inspect_field


def patch(row, schema):
    """按字段策略修补一行。

    绝不因单个字段缺失而丢弃整行：
    - 必填字段缺失/非法 -> 该行进入 isolated（隔离），但可空字段仍照常补齐
    - 可空字段缺失 -> 按类型补默认值
    - 枚举越界 -> 降级
    返回结构：
      {
        "row":      修补后的行（隔离行也包含已补齐的可空字段）,
        "patched":  [被修补/降级/收敛的字段名],
        "isolated": [导致隔离的字段名],
        "sources":  {字段: "original|default|degrade"},
        "fields":   {字段: {action, source, reason, original, value}},
        "is_isolated": bool,
      }
    """
    norm = normalize_schema(schema)
    row = dict(row or {})

    result_row = {}
    patched, isolated, sources, fields = [], [], {}, {}

    for name, spec in norm["fields"].items():
        present = name in row
        value = row[name] if present else MISSING
        outcome, final_value, detail = inspect_field(spec, value)

        sources[name] = detail["source"]
        fields[name] = {
            "action": detail["action"],
            "source": detail["source"],
            "reason": detail["reason"],
            "original": detail["original"],
            "value": final_value,
        }

        if outcome == "isolated":
            isolated.append(name)
            continue  # 必填缺失/非法：不写入该字段
        result_row[name] = final_value
        if outcome == "patched":
            patched.append(name)

    # schema 未声明的字段原样透传，来源记为 original
    for name, value in row.items():
        if name not in norm["fields"]:
            result_row[name] = value
            sources[name] = "original"
            fields[name] = {
                "action": None,
                "source": "original",
                "reason": None,
                "original": value,
                "value": value,
            }

    return {
        "row": result_row,
        "patched": patched,
        "isolated": isolated,
        "sources": sources,
        "fields": fields,
        "is_isolated": bool(isolated),
    }
