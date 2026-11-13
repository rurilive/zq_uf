"""数据血缘，当前只到表级。"""

_jobs = []


def reset():
    _jobs.clear()


def record(job):
    """存输入表和输出表，字段信息丢掉。"""
    entry = {
        "job": job.get("job"),
        "inputs": list(job.get("inputs") or []),
        "outputs": list(job.get("outputs") or []),
    }
    _jobs.append(entry)
    return len(_jobs)


def trace(table, field=None):
    """field 参数被忽略，只按表名匹配。"""
    rows = []
    for entry in _jobs:
        if table in entry["inputs"] or table in entry["outputs"]:
            rows.append(entry)
    return rows
"""字段级血缘：record 落字段边，trace 查表级，trace_field 查字段级上下游。"""

from .errors import JobFormatError, SQLParseError, UnknownFieldError
from .sqlparse import parse_insert_select

# 聚合函数集合：表达式里出现即把边标记为 aggregation。
AGGREGATE_FUNCTIONS = {"COUNT", "SUM", "AVG", "MIN", "MAX", "STDDEV", "VARIANCE", "MEDIAN"}

# trace_field 的默认最大递归层数（阈值），防止环状任务导致无限递归。
DEFAULT_MAX_DEPTH = 10

_JOB_KEYS = {"job", "inputs", "outputs", "columns", "sql", "renames"}

_jobs = []           # [{"job", "inputs", "outputs"}]，trace 的返回行
_edges = []          # [{"job", "from", "to", "expression", "functions", "kind", "alias"}]
_field_attrs = {}    # 输出字段 -> {"expression", "functions", "kind", "job"}
_aliases = {}        # 旧字段全名 -> 新字段全名（改名链）
_alias_history = {}  # 新字段全名 -> [历史名称]


def reset():
    _jobs.clear()
    _edges.clear()
    _field_attrs.clear()
    _aliases.clear()
    _alias_history.clear()


def record(job):
    """记录一个任务。

    job 为 dict，允许键：job（必填）、inputs、outputs、columns、sql、renames。
    columns: [{"from": "表.字段", "to": "表.字段"}] 显式字段映射；
    sql: INSERT INTO ... SELECT ... 语句，解析出字段边、别名与函数；
    renames: [{"table","from","to"} 或 {"from": "表.旧", "to": "表.新"}] 字段改名。
    任何格式问题都抛 JobFormatError/SQLParseError，不静默跳过。
    """
    if not isinstance(job, dict):
        raise TypeError("job 必须是 dict，实际为 %s" % type(job).__name__)
    unknown = set(job) - _JOB_KEYS
    if unknown:
        raise JobFormatError("job 含未知键 %s，支持键 %s" % (sorted(unknown), sorted(_JOB_KEYS)))
    name = job.get("job")
    if not isinstance(name, str) or not name:
        raise JobFormatError("job 缺少非空字符串键 'job'")
    inputs = _table_list(job, "inputs")
    outputs = _table_list(job, "outputs")
    _jobs.append({"job": name, "inputs": inputs, "outputs": outputs})
    for mapping in job.get("columns") or []:
        _record_column_mapping(name, inputs, outputs, mapping)
    if job.get("sql") is not None:
        _record_sql(name, inputs, outputs, job["sql"])
    for rename in job.get("renames") or []:
        _record_rename(name, rename)
    return len(_jobs)


def trace(table, field=None):
    """表级血缘查询，返回 [{"job", "inputs", "outputs"}] 任务行。

    field 为 None 或未命中已知字段时返回涉及该表的全部任务行（旧行为）；
    field 命中时返回该字段上下游链路经过的任务行。旧字段名会经别名历史解析。
    """
    if not isinstance(table, str) or not table:
        raise TypeError("table 必须是非空字符串，实际为 %r" % (table,))
    if field is None:
        return _table_rows(table)
    full = _resolve("%s.%s" % (table, field))
    if full not in _known_fields():
        return _table_rows(table)
    jobs = set()
    for _, edge, _ in _walk(full, "up", DEFAULT_MAX_DEPTH) + _walk(full, "down", DEFAULT_MAX_DEPTH):
        jobs.add(edge["job"])
    return [dict(entry) for entry in _jobs if entry["job"] in jobs]


def trace_field(table, field, max_depth=DEFAULT_MAX_DEPTH):
    """字段级血缘查询：返回上下游字段与层级。

    返回 {"field", "aliases", "kind", "functions", "expression",
    "upstream": [{"field", "level", "job", "kind", "functions"}], "downstream": [...]}。
    字段不存在抛 UnknownFieldError；max_depth 限制递归层数。
    """
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise TypeError("max_depth 必须是 int，实际为 %s" % type(max_depth).__name__)
    if max_depth < 0:
        raise ValueError("max_depth 必须 >= 0，实际为 %d" % max_depth)
    if not isinstance(table, str) or not table or not isinstance(field, str) or not field:
        raise TypeError("table 和 field 必须是非空字符串")
    full = _resolve("%s.%s" % (table, field))
    if full not in _known_fields():
        raise UnknownFieldError("未知字段 %s.%s，可用 fields() 查看已知字段" % (table, field))
    attrs = _field_attrs.get(full, {})
    return {
        "field": full,
        "aliases": list(_alias_history.get(full, [])),
        "kind": attrs.get("kind"),
        "functions": list(attrs.get("functions", [])),
        "expression": attrs.get("expression"),
        "upstream": _walk_items(full, "up", max_depth),
        "downstream": _walk_items(full, "down", max_depth),
    }


def fields(table=None):
    """列出血缘图中的已知字段；table 不为 None 时按表过滤。"""
    known = sorted(_known_fields())
    if table is None:
        return known
    prefix = table + "."
    return [name for name in known if name.startswith(prefix)]


def _table_list(job, key):
    value = job.get(key)
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or any(not isinstance(t, str) or not t for t in value):
        raise JobFormatError("job[%r] 必须是非空字符串列表" % key)
    return list(value)


def _table_rows(table):
    return [dict(e) for e in _jobs if table in e["inputs"] or table in e["outputs"]]


def _split_field(full, what="字段"):
    if not isinstance(full, str) or "." not in full:
        raise JobFormatError("%s必须是 '表.字段' 格式，实际为 %r" % (what, full))
    table, field = full.rsplit(".", 1)
    if not table or not field:
        raise JobFormatError("%s必须是 '表.字段' 格式，实际为 %r" % (what, full))
    return table, field


def _add_edge(job_name, src, dst, expression, functions, kind, alias):
    _edges.append({
        "job": job_name, "from": src, "to": dst,
        "expression": expression, "functions": list(functions),
        "kind": kind, "alias": alias,
    })
    _field_attrs[dst] = {"expression": expression, "functions": list(functions),
                         "kind": kind, "job": job_name}


def _record_column_mapping(job_name, inputs, outputs, mapping):
    if not isinstance(mapping, dict) or set(mapping) != {"from", "to"}:
        raise JobFormatError("columns 项必须是只含 'from'/'to' 的 dict，实际为 %r" % (mapping,))
    src, dst = mapping["from"], mapping["to"]
    src_table, _ = _split_field(src, "columns.from ")
    dst_table, dst_field = _split_field(dst, "columns.to ")
    if src_table not in inputs:
        raise JobFormatError("columns.from 的表 %r 不在 inputs %s 中" % (src_table, inputs))
    if dst_table not in outputs:
        raise JobFormatError("columns.to 的表 %r 不在 outputs %s 中" % (dst_table, outputs))
    _add_edge(job_name, src, dst, expression=None, functions=[], kind="direct", alias=dst_field)


def _record_sql(job_name, inputs, outputs, sql):
    parsed = parse_insert_select(sql)
    target = parsed["target"]
    if target not in outputs:
        raise JobFormatError("SQL 目标表 %r 不在 outputs %s 中" % (target, outputs))
    undeclared = set(parsed["sources"].values()) - set(inputs)
    if undeclared:
        raise JobFormatError("SQL 来源表 %s 不在 inputs %s 中" % (sorted(undeclared), inputs))
    for item in parsed["items"]:
        if item["alias"] is None and not item["lone_column"]:
            raise JobFormatError("表达式 %r 缺少别名（AS）" % item["expression"])
        out_name = item["alias"] or item["columns"][0][1]
        dst = "%s.%s" % (target, out_name)
        functions = item["functions"]
        if any(f in AGGREGATE_FUNCTIONS for f in functions):
            kind = "aggregation"
        elif item["lone_column"]:
            kind = "direct"
        else:
            kind = "expression"
        if not item["columns"]:
            # 例如 count(*) as cnt / 字面量：无来源字段，但保留表达式与函数信息。
            _field_attrs[dst] = {"expression": item["expression"], "functions": list(functions),
                                 "kind": kind, "job": job_name}
        for qualifier, column in item["columns"]:
            src_table = _resolve_table(parsed["sources"], qualifier, column)
            _add_edge(job_name, "%s.%s" % (src_table, column), dst,
                      item["expression"], functions, kind, out_name)


def _resolve_table(sources, qualifier, column):
    if qualifier is None:
        tables = sorted(set(sources.values()))
        if len(tables) == 1:
            return tables[0]
        raise SQLParseError("裸列 %r 无法确定所属表（来源表 %s），请加表名限定" % (column, tables))
    if qualifier not in sources:
        raise SQLParseError("未知表别名 %r（已声明 %s）" % (qualifier, sorted(sources)))
    return sources[qualifier]


def _record_rename(job_name, rename):
    if not isinstance(rename, dict):
        raise JobFormatError("renames 项必须是 dict，实际为 %r" % (rename,))
    if set(rename) == {"table", "from", "to"}:
        table = rename["table"]
        if not isinstance(table, str) or not table:
            raise JobFormatError("renames.table 必须是非空字符串")
        old = "%s.%s" % (table, rename["from"])
        new = "%s.%s" % (table, rename["to"])
    elif set(rename) == {"from", "to"}:
        old, new = rename["from"], rename["to"]
    else:
        raise JobFormatError("renames 项必须含 {'table','from','to'} 或 {'from','to'}")
    old_table, _ = _split_field(old, "renames.from ")
    new_table, new_field = _split_field(new, "renames.to ")
    if old_table != new_table:
        raise JobFormatError("改名必须在同一张表内: %r -> %r" % (old, new))
    known = _known_fields()
    if old not in known:
        raise JobFormatError("改名的原字段 %r 不存在于血缘图中" % old)
    if new in known:
        raise JobFormatError("改名的新字段 %r 已存在" % new)
    _aliases[old] = new
    _alias_history.setdefault(new, [])
    _alias_history[new].extend(_alias_history.get(old, []))
    _alias_history[new].append(old)
    _add_edge(job_name, old, new, expression=None, functions=[], kind="rename", alias=new_field)


def _known_fields():
    known = set(_field_attrs)
    for edge in _edges:
        known.add(edge["from"])
        known.add(edge["to"])
    return known


def _resolve(full):
    seen = set()
    current = full
    while current in _aliases and current not in seen:
        seen.add(current)
        current = _aliases[current]
    return current


def _walk(start, direction, max_depth):
    """BFS 遍历字段图，返回 [(字段, 边, 层级)]，层级从 1 起，去重取最小层级。"""
    results = []
    best = {start: 0}
    frontier = [(start, 0)]
    while frontier:
        current, level = frontier.pop(0)
        if level >= max_depth:
            continue
        for edge in _edges:
            if direction == "up":
                if edge["to"] != current:
                    continue
                nxt = edge["from"]
            else:
                if edge["from"] != current:
                    continue
                nxt = edge["to"]
            if best.get(nxt) is not None and best[nxt] <= level + 1:
                continue
            best[nxt] = level + 1
            results.append((nxt, edge, level + 1))
            frontier.append((nxt, level + 1))
    return results


def _walk_items(full, direction, max_depth):
    items = [
        {"field": name, "level": level, "job": edge["job"],
         "kind": edge["kind"], "functions": list(edge["functions"])}
        for name, edge, level in _walk(full, direction, max_depth)
    ]
    items.sort(key=lambda item: (item["level"], item["field"]))
    return items

