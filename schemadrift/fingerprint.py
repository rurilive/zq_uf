"""表结构指纹：采集列名、类型、默认值与可空性。

指纹口径：
- 每列采集 name/type/nullable/has_default/default_hash/sensitive；
- 默认值一律先做 SHA-256，再参与列指纹与整表指纹，明文不进入指纹；
- 敏感列（显式 sensitive 或列名命中敏感词）的默认值明文不写入快照；
- env 不参与指纹，因此同一结构在不同环境之间可直接比较。
"""

import hashlib
import json
import re

SCHEMA_VERSION = 1

# 列名命中即按敏感列处理；可在列定义里用 sensitive 显式覆盖。
SENSITIVE_NAME_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|"
    r"access[_-]?key|ssn|social[_-]?security|credit[_-]?card|card[_-]?number|cvv)",
    re.IGNORECASE,
)


def _canonical(value):
    """稳定序列化：同一语义值在任何进程/顺序下得到同一字符串。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def is_sensitive_name(name):
    return bool(SENSITIVE_NAME_RE.search(str(name)))


def _normalise_type(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "t")
    return bool(value)


def _column_spec(raw, name=None):
    """把单个列定义规范化为指纹列。

    只给列名（字符串）时视为不完整定义：类型/可空性/默认值均为未知，
    避免把“没有声明”误判成“发生了变化”。
    """
    incomplete = isinstance(raw, str)
    if incomplete:
        raw = {"name": raw}
    if not isinstance(raw, dict):
        raise TypeError("列定义必须是字符串或字典：%r" % (raw,))

    column = name or raw.get("name")
    if not column:
        raise ValueError("列定义缺少 name：%r" % (raw,))
    column = str(column)

    sensitive = raw.get("sensitive")
    if sensitive is None:
        sensitive = is_sensitive_name(column)
    sensitive = bool(sensitive)

    # 显式出现 default 即视为有默认值；default: null 表示 DEFAULT NULL。
    has_default = "default" in raw
    default = raw.get("default") if has_default else None
    default_hash = _digest(default) if has_default else None

    spec = {
        "name": column,
        "type": _normalise_type(raw.get("type")),
        "nullable": _as_bool(raw.get("nullable")),
        "has_default": has_default,
        # 敏感列默认值只保留哈希，快照/报告里不落明文。
        "default": None if sensitive else default,
        "default_hash": default_hash,
        "sensitive": sensitive,
    }
    # 只有原始定义里显式给出的属性才视为已知；缺失属性不参与漂移判定。
    # has_default 例外：未声明 default 本身就表示“无默认值”。
    known = ["name", "has_default", "default_hash"]
    if "type" in raw and raw.get("type") is not None:
        known.append("type")
    if "nullable" in raw:
        known.append("nullable")
    if "default" in raw:
        known.append("default")
    spec["known_attributes"] = known
    # 列指纹只使用 default_hash，保证任何情况下明文都不进入指纹。
    spec["fingerprint"] = _digest({
        "name": spec["name"],
        "type": spec["type"],
        "nullable": spec["nullable"],
        "has_default": spec["has_default"],
        "default_hash": spec["default_hash"],
        "sensitive": spec["sensitive"],
    })
    return spec


def snapshot(table, *, env=None):
    """生成表结构指纹。

    table 支持两种写法：
    - {"name": "users", "env": "prod", "columns": ["id", "note"]}
    - {"name": "users", "env": "prod", "columns": [
          {"name": "id", "type": "bigint", "nullable": False},
          {"name": "note", "type": "text", "nullable": True, "default": ""},
      ]}
    columns 也可以写成 {列名: 列定义} 的映射。

    返回的 "columns" 始终是排序后的列名列表，兼容旧用法。
    """
    if not isinstance(table, dict):
        raise TypeError("table 必须是字典：%r" % (table,))

    # 已经是快照时保持幂等，仅允许覆盖 env。
    if "column_specs" in table and "fingerprint" in table and "columns" in table:
        result = dict(table)
        result["columns"] = sorted(result["columns"])
        if env is not None:
            result["env"] = str(env)
        return result

    name = table.get("name") or table.get("table")
    table_env = env if env is not None else table.get("env")
    table_env = str(table_env or "default")

    raw_columns = table.get("columns") or []
    specs = {}
    if isinstance(raw_columns, dict):
        for key in sorted(raw_columns):
            specs[str(key)] = _column_spec(raw_columns[key], name=key)
    else:
        for raw in raw_columns:
            spec = _column_spec(raw)
            if spec["name"] in specs:
                raise ValueError("重复列名：%s" % spec["name"])
            specs[spec["name"]] = spec

    columns = sorted(specs)
    fingerprint = _digest({
        "table": name,
        "columns": [[column, specs[column]["fingerprint"]] for column in columns],
    })
    return {
        "table": name,
        "env": table_env,
        "schema_version": SCHEMA_VERSION,
        "columns": columns,
        "column_specs": specs,
        "fingerprint": fingerprint,
    }
