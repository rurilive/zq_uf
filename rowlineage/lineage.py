"""行级血缘：主键级边、增量幂等传播、环路截断。

节点: (table, key)，key 为主键值 (str/int/float)。
边:   {"src": (table, key), "dst": (table, key), "job": str, "ts": float}。
"""

import re
import time

__all__ = ["record", "reset", "trace", "propagate", "LineageError"]


class LineageError(ValueError):
    """血缘输入或查询非法时抛出。"""


_jobs = []        # 内部记录: {"job","inputs","outputs","ts"}
_edges = []       # {"src","dst","job","ts"}
_edge_keys = set()  # (src, dst, job) 去重
_key_map = {}     # "table.col" -> "table.col"
_tables = set()
_applied = {}     # update_id -> 传播结果（幂等）

_KEY_TYPES = (str, int, float)
_IDENT = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")
_KEYWORDS = {"where", "join", "on", "left", "right", "inner", "outer",
             "full", "cross", "group", "order", "limit", "as"}

_INSERT_SELECT = re.compile(
    r"insert\s+into\s+(?P<target>[A-Za-z_][\w.]*)\s*"
    r"(?:\((?P<cols>[^)]*)\)\s*)?"
    r"select\s+(?P<select>.*?)\s+from\s+(?P<rest>.*)",
    re.I | re.S)


def reset():
    _jobs.clear()
    _edges.clear()
    _edge_keys.clear()
    _key_map.clear()
    _tables.clear()
    _applied.clear()


# ---------------------------------------------------------------- record

def record(job):
    """记录一个任务，返回任务序号。

    job 字段:
      job/inputs/outputs  表级信息（inputs/outputs 可由 sql 推导）
      keys   {"源表.列": "目标表.列"} 主键映射
      sql    INSERT INTO .. SELECT .. FROM .. [WHERE ..]，解析主键映射与过滤条件
      rows   显式行级边: (src_table, src_key, dst_table, dst_key) 四元组
             或 {"src_table","src_key","dst_table","dst_key"}
      ts     时间戳（默认当前时间）
    """
    if not isinstance(job, dict):
        raise LineageError("job 必须是 dict，得到 %s" % type(job).__name__)
    name = job.get("job")
    ts = job.get("ts", time.time())
    if not isinstance(ts, (int, float)):
        raise LineageError("ts 必须是数字时间戳: %r" % (ts,))
    inputs = list(job.get("inputs") or [])
    outputs = list(job.get("outputs") or [])
    mapping = {}
    filters = {}
    sql = job.get("sql")
    if sql is not None:
        parsed = _parse_sql(sql)
        if parsed["target"] not in outputs:
            outputs.append(parsed["target"])
        for t in parsed["sources"]:
            if t not in inputs:
                inputs.append(t)
        mapping.update(parsed["mapping"])
        for col, vals in parsed["filters"].items():
            filters.setdefault(col, []).extend(vals)
    _check_tables(inputs, "inputs")
    _check_tables(outputs, "outputs")
    if not inputs or not outputs:
        raise LineageError("job 必须包含非空 inputs 和 outputs")
    keys = job.get("keys") or {}
    if not isinstance(keys, dict):
        raise LineageError("keys 必须是 dict: {'源表.列': '目标表.列'}")
    for src, dst in keys.items():
        _check_col(src)
        _check_col(dst)
        mapping[src] = dst
    _jobs.append({"job": name, "inputs": inputs, "outputs": outputs, "ts": ts})
    _tables.update(inputs)
    _tables.update(outputs)
    _key_map.update(mapping)
    for r in job.get("rows") or []:
        st, sk, dt, dk = _check_row(r)
        _add_edge(st, sk, dt, dk, name, ts)
    # 由过滤条件派生行级边：主键值经映射保持不变
    for src_col, values in filters.items():
        dst_col = mapping.get(src_col)
        if dst_col is None:
            continue
        st = src_col.rsplit(".", 1)[0]
        dt = dst_col.rsplit(".", 1)[0]
        for v in values:
            _add_edge(st, v, dt, v, name, ts)
    return len(_jobs)


def _check_tables(tables, field):
    for t in tables:
        if not isinstance(t, str) or not t:
            raise LineageError("%s 含有非法表名: %r" % (field, t))


def _check_col(col):
    if not isinstance(col, str) or "." not in col.strip("."):
        raise LineageError("列引用必须是 '表.列' 格式: %r" % (col,))


def _check_key(key):
    if not isinstance(key, _KEY_TYPES) or isinstance(key, bool):
        raise LineageError("主键值必须是 str/int/float: %r" % (key,))


def _check_row(r):
    if isinstance(r, dict):
        try:
            row = (r["src_table"], r["src_key"], r["dst_table"], r["dst_key"])
        except KeyError as e:
            raise LineageError("rows 缺字段: %s" % e)
    elif isinstance(r, (list, tuple)) and len(r) == 4:
        row = tuple(r)
    else:
        raise LineageError("rows 项必须是四元组或 dict: %r" % (r,))
    st, sk, dt, dk = row
    _check_tables([st, dt], "rows")
    _check_key(sk)
    _check_key(dk)
    return row


def _add_edge(st, sk, dt, dk, job, ts):
    sig = ((st, sk), (dt, dk), job)
    if sig in _edge_keys:
        return
    _edge_keys.add(sig)
    _edges.append({"src": (st, sk), "dst": (dt, dk), "job": job, "ts": ts})


# ---------------------------------------------------------------- SQL

def _parse_sql(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise LineageError("sql 必须是非空字符串")
    m = _INSERT_SELECT.search(sql)
    if not m:
        raise LineageError("无法解析 SQL：需要 INSERT INTO .. SELECT .. FROM ..: %r" % sql)
    target = m.group("target")
    rest = m.group("rest").rstrip().rstrip(";")
    wm = re.search(r"\bwhere\b", rest, re.I)
    from_part = rest[:wm.start()] if wm else rest
    where_part = rest[wm.end():] if wm else ""
    fm = re.match(r"\s*([A-Za-z_][\w.]*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?",
                  from_part, re.I)
    if not fm:
        raise LineageError("无法解析 SQL 的 FROM 子句: %r" % from_part)
    primary = fm.group(1)
    aliases = {primary: primary}
    a = fm.group(2)
    if a and a.lower() not in _KEYWORDS:
        aliases[a] = primary
    sources = [primary]
    for jm in re.finditer(
            r"\bjoin\s+([A-Za-z_][\w.]*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?",
            from_part, re.I):
        t = jm.group(1)
        sources.append(t)
        aliases[t] = t
        ja = jm.group(2)
        if ja and ja.lower() not in _KEYWORDS:
            aliases[ja] = t
    select_items = [s.strip() for s in m.group("select").split(",") if s.strip()]
    insert_cols = ([c.strip() for c in m.group("cols").split(",") if c.strip()]
                   if m.group("cols") else [])
    if insert_cols and len(insert_cols) != len(select_items):
        raise LineageError("INSERT 列数(%d)与 SELECT 列数(%d)不一致"
                           % (len(insert_cols), len(select_items)))
    mapping = {}
    for i, item in enumerate(select_items):
        cm = re.match(r"^(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)$", item)
        if not cm:
            continue  # 表达式列无法静态映射，跳过
        col_alias, col = cm.groups()
        if col_alias and col_alias not in aliases:
            raise LineageError("未知表别名: %r" % col_alias)
        src_table = aliases[col_alias] if col_alias else primary
        dst_col = insert_cols[i] if insert_cols else col
        mapping["%s.%s" % (src_table, col)] = "%s.%s" % (target, dst_col)
    filters = {}
    for cond in re.split(r"\s+and\s+", where_part, flags=re.I):
        cond = _strip_parens(cond.strip())
        if not cond:
            continue
        im = re.match(r"^(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s+in\s*\(([^)]*)\)$",
                      cond, re.I)
        em = re.match(r"^(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*=\s*(.+)$",
                      cond, re.I)
        if im:
            col_alias, col, vals = im.groups()
            values = [_literal(v) for v in vals.split(",")]
        elif em:
            col_alias, col, val = em.groups()
            values = [_literal(val)]
        else:
            continue  # 不支持的谓词（BETWEEN/>/</OR 等），跳过
        values = [v for v in values if v is not None]
        if not values:
            continue
        if col_alias and col_alias not in aliases:
            raise LineageError("未知表别名: %r" % col_alias)
        src_table = aliases[col_alias] if col_alias else primary
        filters.setdefault("%s.%s" % (src_table, col), []).extend(values)
    return {"target": target, "sources": sources,
            "mapping": mapping, "filters": filters}


def _strip_parens(s):
    """只剥离成对包裹整个表达式的外层括号。"""
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        balanced = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    balanced = False
                    break
        if not balanced:
            break
        s = s[1:-1].strip()
    return s


def _literal(text):
    """解析 WHERE 里的常量；列引用返回 None，无法解析抛错。"""
    text = text.strip()
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if _IDENT.match(text):
        return None  # 列引用（如 join 条件），非常量
    raise LineageError("无法解析过滤值: %r" % text)


# ---------------------------------------------------------------- 查询

def trace(table, key=None, tables=None, start=None, end=None):
    """查询血缘。

    key=None: 表级，返回 [{"job","inputs","outputs"}]（按 ts 过滤）。
    key 给定: 行级，返回 {"table","key","upstream","downstream","paths",
    "truncated"}；该 (table,key) 无行级边时退化为表级。
    tables/start/end: 按表集合与边时间范围过滤。
    """
    if not isinstance(table, str) or not table:
        raise LineageError("table 必须是非空字符串: %r" % (table,))
    if table not in _tables:
        raise LineageError("未知表: %r" % table)
    table_set = _check_table_filter(tables)
    _check_time_range(start, end)
    if key is None:
        return [
            {"job": j["job"], "inputs": list(j["inputs"]),
             "outputs": list(j["outputs"])}
            for j in _jobs
            if (table in j["inputs"] or table in j["outputs"])
            and _in_range(j["ts"], start, end)
            and (table_set is None or table in table_set)
        ]
    _check_key(key)
    edges = _select_edges(table_set, start, end)
    node = (table, key)
    if not any(node in (e["src"], e["dst"]) for e in edges):
        return trace(table, tables=tables, start=start, end=end)  # 无行级数据
    up_levels, up_paths, up_trunc = _walk(node, edges, "up")
    down_levels, down_paths, down_trunc = _walk(node, edges, "down")
    return {
        "table": table,
        "key": key,
        "upstream": up_levels,
        "downstream": down_levels,
        "paths": {"upstream": up_paths, "downstream": down_paths},
        "truncated": _nodes(up_trunc + down_trunc),
    }


def propagate(table, key, update_id=None, tables=None, start=None, end=None):
    """从 (table, key) 沿边向下游增量传播一次更新。

    返回 {"origin","updated","truncated","visited","idempotent"}。
    update_id 已传播过时直接返回空 updated（幂等），visited 保留已访问集合。
    环路经 visited 截断，截断点记入 truncated。
    """
    if not isinstance(table, str) or not table:
        raise LineageError("table 必须是非空字符串: %r" % (table,))
    if table not in _tables:
        raise LineageError("未知表: %r" % table)
    _check_key(key)
    if update_id is not None and update_id in _applied:
        prev = _applied[update_id]
        return {"origin": prev["origin"], "updated": [],
                "truncated": prev["truncated"], "visited": prev["visited"],
                "idempotent": True}
    edges = _select_edges(_check_table_filter(tables), start, end)
    levels, _paths, truncated = _walk((table, key), edges, "down")
    result = {
        "origin": {"table": table, "key": key},
        "updated": levels,
        "truncated": _nodes(truncated),
        "visited": _nodes([(table, key)] +
                          [(n["table"], n["key"]) for n in levels]),
        "idempotent": False,
    }
    if update_id is not None:
        _applied[update_id] = result
    return result


# ---------------------------------------------------------------- 内部

def _check_table_filter(tables):
    if tables is None:
        return None
    table_set = set(tables)
    for t in table_set:
        if not isinstance(t, str) or not t:
            raise LineageError("tables 含有非法表名: %r" % (t,))
    return table_set


def _check_time_range(start, end):
    for v in (start, end):
        if v is not None and not isinstance(v, (int, float)):
            raise LineageError("start/end 必须是数字时间戳: %r" % (v,))
    if start is not None and end is not None and start > end:
        raise LineageError("start(%r) 不能大于 end(%r)" % (start, end))


def _in_range(ts, start, end):
    return (start is None or ts >= start) and (end is None or ts <= end)


def _select_edges(table_set, start, end):
    return [
        e for e in _edges
        if _in_range(e["ts"], start, end)
        and (table_set is None
             or (e["src"][0] in table_set and e["dst"][0] in table_set))
    ]


def _nodes(tuples):
    seen, out = set(), []
    for t, k in tuples:
        if (t, k) not in seen:
            seen.add((t, k))
            out.append({"table": t, "key": k})
    return out


def _walk(start, edges, direction):
    """BFS 算层级 + DFS 枚举路径；visited 截断环路。

    返回 (levels, paths, truncated)。paths 中截断路径以重复节点结尾。
    """
    adj = {}
    for e in edges:
        a, b = ((e["dst"], e["src"]) if direction == "up"
                else (e["src"], e["dst"]))
        adj.setdefault(a, []).append(b)
    levels = []
    seen = {start}
    truncated = []
    frontier = [(start, 0)]
    while frontier:
        node, lv = frontier.pop(0)
        for nxt in adj.get(node, []):
            if nxt in seen:
                truncated.append(nxt)  # 环路截断
                continue
            seen.add(nxt)
            levels.append({"table": nxt[0], "key": nxt[1], "level": lv + 1})
            frontier.append((nxt, lv + 1))
    paths = []

    def dfs(node, path, in_path):
        children = adj.get(node, [])
        if not children:
            paths.append(path)
            return
        for nxt in children:
            if nxt in in_path:
                paths.append(path + [nxt])  # 截断路径，以重复节点结尾
            else:
                dfs(nxt, path + [nxt], in_path | {nxt})

    dfs(start, [start], {start})
    paths = [[{"table": t, "key": k} for t, k in p] for p in paths if len(p) > 1]
    return levels, paths, truncated
