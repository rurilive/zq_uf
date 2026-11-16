"""敏感列发现：内容采样为主，列名和注释辅助。

命中给出行列位置、等级、依据和样例；同一类数据在不同表里归到同一等级。
人工调整会被记住，之后可以只重扫受影响的表。
"""

import re

# 敏感类别 -> 默认等级。等级只由类别决定，跨表一致。
_LEVELS = {
    "id": "高",       # 证件号
    "account": "高",  # 账号
    "contact": "中",  # 联系方式
    "address": "低",  # 地址
}

_KIND_LABELS = {
    "id": "证件号",
    "contact": "联系方式",
    "address": "地址",
    "account": "账号",
}

# 内容模式，按优先级匹配：身份证 18 位先于银行卡号，避免互相吃掉。
_CONTENT_PATTERNS = [
    ("id", "id_card", re.compile(r"^\d{17}[\dXx]$")),
    ("contact", "phone", re.compile(r"^1[3-9]\d{9}$")),
    ("contact", "email", re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")),
    ("account", "bank_card", re.compile(r"^\d{12,19}$")),
    ("address", "address_text", re.compile(r"(省|市|区|县|镇).*(路|街|巷|号)")),
]

# 列名 / 注释关键词，作为内容之外的辅助依据。
_NAME_KEYWORDS = {
    "id": ("idcard", "id_card", "id_no", "cert_no", "身份证", "证件"),
    "contact": ("phone", "mobile", "tel", "email", "手机", "电话", "邮箱", "联系方式"),
    "address": ("address", "addr", "地址", "住址"),
    "account": ("account", "acct", "bank_card", "card_no", "账号", "账户", "银行卡"),
}

_MAX_SAMPLES = 5

# 表名 -> 最近一次扫描的命中列
_RESULTS = {}
# 表名 -> 原始表结构，供重扫
_TABLES = {}
# (表名, 列名) -> 人工调整
_OVERRIDES = {}
# 有调整、尚未重扫的表
_AFFECTED = set()


def _database_of(table_name):
    return table_name.split(".", 1)[0] if "." in table_name else ""


def _content_hits(values):
    """对采样值跑内容模式，返回 kind -> [(row, value, pattern)]。"""
    hits_by_kind = {}
    for row, value in enumerate(values):
        text = str(value).strip()
        if not text:
            continue
        for kind, pattern_name, pattern in _CONTENT_PATTERNS:
            if pattern.search(text):
                hits_by_kind.setdefault(kind, []).append((row, text, pattern_name))
                break
    return hits_by_kind


def _name_hits(column_name, comment):
    """列名和注释关键词命中，返回 [(kind, 依据)]。"""
    found = []
    lowered = column_name.lower()
    for kind, keywords in _NAME_KEYWORDS.items():
        for keyword in keywords:
            if keyword in lowered:
                found.append((kind, "name:%s" % keyword))
                break
            if keyword in comment:
                found.append((kind, "comment:%s" % keyword))
                break
    return found


def _scan_column(database, table_name, index, column):
    column_name = str(column.get("name", ""))
    comment = str(column.get("comment", ""))
    values = column.get("values") or []

    content = _content_hits(values)
    named = _name_hits(column_name, comment)
    if not content and not named:
        return None

    # 内容命中优先定类；只有命名命中时按类别优先级取第一个。
    if content:
        kind = max(content, key=lambda k: len(content[k]))
    else:
        kind = named[0][0]

    evidence = []
    for _, _, pattern_name in content.get(kind, []):
        item = "content:%s" % pattern_name
        if item not in evidence:
            evidence.append(item)
    for hit_kind, basis in named:
        if hit_kind == kind and basis not in evidence:
            evidence.append(basis)

    matched = content.get(kind, [])
    samples = [{"row": row, "value": value} for row, value, _ in matched[:_MAX_SAMPLES]]

    level = _LEVELS[kind]
    adjusted = False
    override = _OVERRIDES.get((table_name, column_name))
    if override is not None:
        if override.get("sensitive") is False:
            return None
        kind = override.get("kind") or kind
        level = override.get("level") or _LEVELS[kind]
        adjusted = True

    return {
        "table": table_name,
        "database": database,
        "column": column_name,
        "index": index,
        "kind": kind,
        "kind_label": _KIND_LABELS[kind],
        "level": level,
        "evidence": evidence,
        "samples": samples,
        "matched_rows": [row for row, _, _ in matched],
        "adjusted": adjusted,
    }


def discover(table):
    """扫描一张表，返回敏感列、等级、命中依据和样例。"""
    if not isinstance(table, dict):
        raise ValueError("table must be a dict")
    columns = table.get("columns")
    if not isinstance(columns, list):
        raise ValueError("columns must be a list")
    name = str(table.get("name", ""))
    database = _database_of(name)
    found = []
    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            continue
        finding = _scan_column(database, name, index, column)
        if finding is not None:
            found.append(finding)
    _TABLES[name] = table
    _RESULTS[name] = found
    _AFFECTED.discard(name)
    return {
        "table": name,
        "database": database,
        "sensitive": len(found),
        "columns": [dict(item) for item in found],
    }


def hits(table_name):
    """按表名取命中列，没有就返回空列表。"""
    return [dict(item) for item in _RESULTS.get(table_name, [])]


def samples(table_name, column):
    """按列查看样例。"""
    for item in _RESULTS.get(table_name, []):
        if item["column"] == column:
            return [dict(sample) for sample in item["samples"]]
    return []


def adjust(table_name, column, level=None, kind=None, sensitive=True):
    """人工调整认定结果并记住；之后用 rescan() 只重扫受影响的表。"""
    if kind is not None and kind not in _LEVELS:
        raise ValueError("unknown kind: %s" % kind)
    _OVERRIDES[(table_name, column)] = {
        "level": level,
        "kind": kind,
        "sensitive": sensitive,
    }
    _AFFECTED.add(table_name)


def rescan():
    """只重扫有人工调整的表，返回实际重扫的表名。"""
    rescanned = []
    for name in sorted(_AFFECTED):
        table = _TABLES.get(name)
        if table is not None:
            discover(table)
            rescanned.append(name)
    return rescanned


def report():
    """按库汇总：敏感列数量、涉及表数量、等级分布。"""
    databases = {}
    for table_name, found in _RESULTS.items():
        if not found:
            continue
        database = _database_of(table_name)
        entry = databases.setdefault(database, {"tables": 0, "sensitive": 0, "levels": {}})
        entry["tables"] += 1
        entry["sensitive"] += len(found)
        for item in found:
            entry["levels"][item["level"]] = entry["levels"].get(item["level"], 0) + 1
    return {
        "databases": databases,
        "tables": sum(entry["tables"] for entry in databases.values()),
        "sensitive": sum(entry["sensitive"] for entry in databases.values()),
    }


def export(database=None):
    """分级结果按库导出成清单；不传 database 则导出全部。"""
    rows = []
    for table_name in sorted(_RESULTS):
        if database is not None and _database_of(table_name) != database:
            continue
        for item in _RESULTS[table_name]:
            rows.append({
                "database": item["database"],
                "table": item["table"],
                "column": item["column"],
                "kind": item["kind"],
                "kind_label": item["kind_label"],
                "level": item["level"],
                "evidence": list(item["evidence"]),
                "samples": len(item["samples"]),
                "adjusted": item["adjusted"],
            })
    return rows
