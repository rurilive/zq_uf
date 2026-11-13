"""数据管道依赖视图：任务 / 表 / 字段三级节点，配置加运行记录补全依赖。

节点类型：
- 任务节点：job 的 name，如 "clean"
- 表节点：读写的表名，如 "orders"
- 字段节点："表.字段"，如 "orders.amount"

所有异常输入都会抛出 ViewError，绝不静默跳过。
"""

import json

__all__ = ["build", "impact", "upstream", "export", "load", "ViewError"]


class ViewError(ValueError):
    """构建或查询视图时的明确错误。"""


def _empty_view():
    return {
        "nodes": [],        # 任务节点（字符串，保持声明顺序）
        "edges": [],        # 任务级边 (src, dst)，含运行记录补全
        "tables": [],       # 表节点
        "fields": [],       # 字段节点 "table.field"
        "table_edges": [],  # 表 <-> 任务
        "field_edges": [],  # 字段 <-> 任务
        "inferred": [],     # 其中由配置读写 / 运行记录补全出的任务边
    }


_current = _empty_view()
_nodes = set()
_graph = {}
_reverse = {}


def _check_name(kind, name):
    if not isinstance(name, str) or not name:
        raise ViewError("%s名非法: %r" % (kind, name))
    if "." in name:
        raise ViewError("%s名不能包含 '.': %s" % (kind, name))
    return name


def _norm_io(entry, job_name):
    """把 reads/writes 条目归一化成 (table, fields)，非法即报错。"""
    if isinstance(entry, str):
        return _check_name("表", entry), []
    if isinstance(entry, dict) and "table" in entry:
        table = _check_name("表", entry["table"])
        fields = entry.get("fields") or []
        if not isinstance(fields, list):
            raise ViewError("任务 %s 的 %s.fields 必须是列表" % (job_name, table))
        return table, [_check_name("字段", field) for field in fields]
    raise ViewError("任务 %s 的读写条目非法: %r" % (job_name, entry))


def _add(seq, item):
    if item not in seq:
        seq.append(item)


def _set_current(view):
    global _current, _nodes, _graph, _reverse
    _current = view
    _nodes = set(view["nodes"]) | set(view["tables"]) | set(view["fields"])
    _graph = {node: set() for node in _nodes}
    for key in ("edges", "table_edges", "field_edges"):
        for src, dst in view[key]:
            _graph.setdefault(src, set()).add(dst)
            _graph.setdefault(dst, set())
    _reverse = {node: set() for node in _graph}
    for src, dsts in _graph.items():
        for dst in dsts:
            _reverse.setdefault(dst, set()).add(src)


def _public(view):
    return {key: list(value) for key, value in view.items()}


def build(jobs, runs=None, min_runs=1):
    """构建两级视图。

    jobs: 配置，每项 {"name", "depends"?, "reads"?, "writes"?}；
          reads/writes 条目为表名或 {"table", "fields"?}。
    runs: 运行记录，每项 {"job", "reads"?, "writes"?}，用于补全实际依赖。
    min_runs: 推断一条任务边所需的最少观测次数（配置声明计 1 次），默认 1。
    """
    if not isinstance(jobs, list) or not jobs:
        raise ViewError("jobs 必须是非空列表")
    if not isinstance(min_runs, int) or min_runs < 1:
        raise ViewError("min_runs 必须是 >= 1 的整数")

    names = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ViewError("任务定义必须是 dict: %r" % (job,))
        name = _check_name("任务", job.get("name"))
        if name in names:
            raise ViewError("任务名重复: %s" % name)
        names.append(name)
    name_set = set(names)

    edges = []
    for job in jobs:
        depends = job.get("depends") or []
        if not isinstance(depends, list):
            raise ViewError("任务 %s 的 depends 必须是列表" % job["name"])
        for dep in depends:
            if dep not in name_set:
                raise ViewError("任务 %s 依赖未知任务 %r" % (job["name"], dep))
            _add(edges, (dep, job["name"]))

    tables, fields = [], []
    table_edges, field_edges = [], []
    config_writers = {}
    config_reads = {}

    for job in jobs:
        name = job["name"]
        reads = [_norm_io(entry, name) for entry in job.get("reads") or []]
        writes = [_norm_io(entry, name) for entry in job.get("writes") or []]
        config_reads[name] = {table for table, _ in reads}
        for table, flds in reads:
            if table in name_set:
                raise ViewError("表名与任务名冲突: %s" % table)
            _add(tables, table)
            _add(table_edges, (table, name))
            for field in flds:
                _add(fields, "%s.%s" % (table, field))
                _add(field_edges, ("%s.%s" % (table, field), name))
        for table, flds in writes:
            if table in name_set:
                raise ViewError("表名与任务名冲突: %s" % table)
            _add(tables, table)
            _add(table_edges, (name, table))
            config_writers.setdefault(table, set()).add(name)
            for field in flds:
                _add(fields, "%s.%s" % (table, field))
                _add(field_edges, (name, "%s.%s" % (table, field)))

    run_list = list(runs or [])
    run_writes = {}
    run_reads = []
    for run in run_list:
        if not isinstance(run, dict):
            raise ViewError("运行记录必须是 dict: %r" % (run,))
        job = run.get("job")
        if job not in name_set:
            raise ViewError("运行记录指向未知任务: %r" % (job,))
        reads = [_norm_io(entry, job)[0] for entry in run.get("reads") or []]
        writes = [_norm_io(entry, job)[0] for entry in run.get("writes") or []]
        run_reads.append((job, reads))
        for table in writes:
            run_writes.setdefault(table, set()).add(job)

    evidence = {}
    for job, reads in run_reads:
        for table in reads:
            for writer in config_writers.get(table, set()) | run_writes.get(table, set()):
                if writer != job:
                    evidence[(writer, job)] = evidence.get((writer, job), 0) + 1
    for name, read_tables in config_reads.items():
        for table in read_tables:
            for writer in config_writers.get(table, set()):
                if writer != name:
                    evidence[(writer, name)] = evidence.get((writer, name), 0) + 1

    config_edge_set = set(edges)
    inferred = sorted(
        edge for edge, count in evidence.items()
        if count >= min_runs and edge not in config_edge_set
    )
    for edge in inferred:
        _add(edges, edge)

    view = {
        "nodes": names,
        "edges": edges,
        "tables": tables,
        "fields": fields,
        "table_edges": table_edges,
        "field_edges": field_edges,
        "inferred": inferred,
    }
    _set_current(view)
    return _public(view)


def _resolve(node):
    if not isinstance(node, str):
        raise ViewError("节点名必须是字符串: %r" % (node,))
    if node not in _nodes:
        raise ViewError("未知节点: %r（当前视图共 %d 个节点）" % (node, len(_nodes)))
    return node


def _check_depth(depth):
    if depth is not None and (not isinstance(depth, int) or depth < 1):
        raise ViewError("depth 必须是 >= 1 的整数或 None")


def _walk(node, depth, reverse=False):
    node = _resolve(node)
    _check_depth(depth)
    graph = _reverse if reverse else _graph
    levels = {}
    frontier = [node]
    current = 0
    while frontier and (depth is None or current < depth):
        current += 1
        nxt = []
        for item in frontier:
            for succ in sorted(graph.get(item, ())):
                if succ != node and succ not in levels:
                    levels[succ] = current
                    nxt.append(succ)
        frontier = nxt
    return levels


def _paths(node, depth, reverse=False):
    graph = _reverse if reverse else _graph
    limit = depth if depth is not None else len(_nodes)
    out = []

    def dfs(cur, path):
        for nxt in sorted(graph.get(cur, ())):
            if nxt in path:
                continue
            new_path = path + [nxt]
            out.append(new_path)
            if len(new_path) - 1 < limit:
                dfs(nxt, new_path)

    dfs(node, [node])
    out.sort()
    return out


def _sorted_levels(levels):
    return dict(sorted(levels.items(), key=lambda kv: (kv[1], kv[0])))


def impact(node, depth=1, paths=False):
    """下游影响查询。

    depth=1（默认）只返回直接下游；depth=None 返回全部传递下游。
    paths=False（默认）返回排序后的节点列表；
    paths=True 返回 {"paths": 所有简单路径, "levels": 节点 -> 层级}。
    """
    levels = _walk(node, depth)
    if not paths:
        return sorted(levels)
    return {"paths": _paths(node, depth), "levels": _sorted_levels(levels)}


def upstream(node, depth=1, paths=False):
    """上游来源查询，参数口径与 impact 相同。"""
    levels = _walk(node, depth, reverse=True)
    if not paths:
        return sorted(levels)
    return {"paths": _paths(node, depth, reverse=True), "levels": _sorted_levels(levels)}


def export(path=None):
    """导出当前视图为可 JSON 序列化的 dict；给定 path 时同时写入文件。"""
    data = {}
    for key, value in _current.items():
        data[key] = [list(item) if isinstance(item, tuple) else item for item in value]
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return data


def load(data):
    """从 export() 产出的 dict 恢复视图，重启后可复现查询结果。"""
    if not isinstance(data, dict):
        raise ViewError("load 需要 export() 产出的 dict")
    view = _empty_view()
    for key in view:
        if key not in data:
            raise ViewError("导出数据缺少字段: %s" % key)
        value = data[key]
        if not isinstance(value, list):
            raise ViewError("导出字段 %s 必须是列表" % key)
        if key in ("edges", "table_edges", "field_edges", "inferred"):
            view[key] = [tuple(entry) for entry in value]
            for entry in view[key]:
                if len(entry) != 2 or not all(isinstance(x, str) for x in entry):
                    raise ViewError("导出字段 %s 含非法边: %r" % (key, list(entry)))
        else:
            if not all(isinstance(x, str) for x in value):
                raise ViewError("导出字段 %s 必须全是字符串" % key)
            view[key] = list(value)
    nodes = set(view["nodes"]) | set(view["tables"]) | set(view["fields"])
    if len(nodes) != len(view["nodes"]) + len(view["tables"]) + len(view["fields"]):
        raise ViewError("导出数据存在节点名冲突")
    for key in ("edges", "table_edges", "field_edges", "inferred"):
        for src, dst in view[key]:
            if src not in nodes or dst not in nodes:
                raise ViewError("导出边 (%s, %s) 指向未知节点" % (src, dst))
    _set_current(view)
    return _public(view)

