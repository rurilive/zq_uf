"""可追溯的血缘图：解析配置与 SQL，消解临时表，支持别名与多跳展开。"""

import re

__all__ = ["Lineage", "ImpactResult", "parse_sql"]

_TOKEN = re.compile(r'"[^"]*"|`[^`]*`|[A-Za-z_][\w$]*|[().,;]')
_NAME_START = re.compile(r'[A-Za-z_"`]')
_CLAUSE_WORDS = {
    "where", "group", "order", "having", "limit", "offset", "on", "using",
    "join", "left", "right", "inner", "outer", "full", "cross", "natural",
    "union", "intersect", "except", "select", "insert", "create", "with",
    "set", "values", "as", "and", "or", "when", "case",
}


def _normalize(name):
    parts = [p.strip('`"').strip().lower() for p in str(name).split(".")]
    return ".".join(p for p in parts if p)


def _is_name(token):
    return bool(token) and bool(_NAME_START.match(token))


def _tokenize(sql):
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'(?:''|[^'])*'", " ", sql)
    return _TOKEN.findall(sql)


def _skip_parens(tokens, i):
    depth = 0
    while i < len(tokens):
        if tokens[i] == "(":
            depth += 1
        elif tokens[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _read_name(tokens, i):
    parts = []
    while i < len(tokens) and _is_name(tokens[i]):
        parts.append(tokens[i])
        i += 1
        if i < len(tokens) and tokens[i] == ".":
            i += 1
            continue
        break
    return (_normalize(".".join(parts)) if parts else ""), i


def _skip_alias(tokens, i):
    if i < len(tokens) and tokens[i].lower() == "as":
        i += 1
    if i < len(tokens) and _is_name(tokens[i]) and tokens[i].lower() not in _CLAUSE_WORDS:
        i += 1
    return i


def parse_sql(sql):
    """从 SQL 中识别输入表、输出表和临时表（CTE / TEMP TABLE / 任务内自产自销）。"""
    tokens = _tokenize(sql)
    inputs, outputs, temps = [], [], set()

    for i, tok in enumerate(tokens):
        if tok.lower() != "with":
            continue
        j = i + 1
        while j < len(tokens) and _is_name(tokens[j]):
            name = _normalize(tokens[j])
            j += 1
            if j < len(tokens) and tokens[j].lower() == "as":
                j += 1
            if j < len(tokens) and tokens[j] == "(":
                temps.add(name)
                j = _skip_parens(tokens, j)
            if j < len(tokens) and tokens[j] == ",":
                j += 1
                continue
            break

    i = 0
    while i < len(tokens):
        tok = tokens[i].lower()
        if tok in ("from", "join"):
            i += 1
            while i < len(tokens):
                if tokens[i] == "(":
                    i += 1  # 子查询内容交给主循环扫描
                    break
                if not _is_name(tokens[i]):
                    break
                name, i = _read_name(tokens, i)
                if name:
                    inputs.append(name)
                i = _skip_alias(tokens, i)
                if i < len(tokens) and tokens[i] == ",":
                    i += 1
                    continue
                break
        elif tok == "insert":
            i += 1
            if i < len(tokens) and tokens[i].lower() == "overwrite":
                i += 1
                if i < len(tokens) and tokens[i].lower() == "table":
                    i += 1
            elif i < len(tokens) and tokens[i].lower() == "into":
                i += 1
            name, i = _read_name(tokens, i)
            if name:
                outputs.append(name)
        elif tok == "create":
            i += 1
            is_temp = False
            while i < len(tokens) and tokens[i].lower() in (
                "or", "replace", "global", "local", "temp", "temporary", "transient",
            ):
                if tokens[i].lower() in ("temp", "temporary"):
                    is_temp = True
                i += 1
            if i < len(tokens) and tokens[i].lower() in ("table", "view"):
                i += 1
            if [t.lower() for t in tokens[i:i + 3]] == ["if", "not", "exists"]:
                i += 3
            name, i = _read_name(tokens, i)
            if name:
                if is_temp:
                    temps.add(name)
                else:
                    outputs.append(name)
        else:
            i += 1

    in_set = set(inputs)
    out_set = set(outputs)
    temps |= in_set & out_set
    return {
        "inputs": [t for t in dict.fromkeys(inputs) if t not in temps],
        "outputs": [t for t in dict.fromkeys(outputs) if t not in temps],
        "temps": sorted(temps),
    }


class ImpactResult(list):
    """受影响任务的有序列表，附带每个任务所在的层级。"""

    def __init__(self, direction="downstream"):
        super().__init__()
        self.direction = direction
        self.levels = {}


class Lineage:
    def __init__(self):
        self.records = {}
        self.edges = []
        self.aliases = {}

    def _resolve(self, name):
        seen = set()
        while name in self.aliases and name not in seen:
            seen.add(name)
            name = self.aliases[name]
        return name

    def rename(self, old, new):
        """表改名：保留历史别名，已存的一跳关系改指向新名。"""
        old_n = _normalize(old)
        new_n = self._resolve(_normalize(new))
        self.aliases[old_n] = new_n
        for rec in self.records.values():
            rec["inputs"] = [new_n if t == old_n else t for t in rec["inputs"]]
            rec["outputs"] = [new_n if t == old_n else t for t in rec["outputs"]]
        for edge in self.edges:
            if edge["source"] == old_n:
                edge["source"] = new_n
            if edge["target"] == old_n:
                edge["target"] = new_n
        return new_n

    def record(self, job):
        name = job["name"]
        for old, new in dict(job.get("renames", {})).items():
            self.rename(old, new)
        inputs = [_normalize(t) for t in job.get("inputs", [])]
        outputs = [_normalize(t) for t in job.get("outputs", [])]
        temps = set()
        if job.get("sql"):
            parsed = parse_sql(job["sql"])
            inputs += parsed["inputs"]
            outputs += parsed["outputs"]
            temps |= set(parsed["temps"])
        temps |= set(inputs) & set(outputs)
        inputs = [self._resolve(t) for t in dict.fromkeys(inputs) if t not in temps]
        outputs = [self._resolve(t) for t in dict.fromkeys(outputs) if t not in temps]
        item = {"inputs": inputs, "outputs": outputs, "temps": sorted(temps)}
        self.records[name] = item
        for source in inputs:
            for target in outputs:
                self.edges.append({"job": name, "source": source, "target": target})
        return item

    def impact(self, table, direction="downstream"):
        """按图展开多跳血缘，返回受影响任务列表（含 levels 层级）。"""
        if direction not in ("downstream", "upstream"):
            raise ValueError("direction 只能是 downstream 或 upstream")
        start = self._resolve(_normalize(table))
        result = ImpactResult(direction)
        frontier = {start}
        seen = set()
        level = 0
        while frontier:
            level += 1
            next_frontier = set()
            for name, rec in self.records.items():
                if name in seen:
                    continue
                if direction == "downstream":
                    hit, grown = frontier.intersection(rec["inputs"]), rec["outputs"]
                else:
                    hit, grown = frontier.intersection(rec["outputs"]), rec["inputs"]
                if hit:
                    seen.add(name)
                    result.append(name)
                    result.levels[name] = level
                    next_frontier.update(grown)
            frontier = next_frontier
        return result
