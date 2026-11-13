"""血缘图：解析任务配置与 SQL，落一跳边，查询时按方向多跳展开。"""

import re

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_CREATE_RE = re.compile(
    r"\bcreate\s+(?P<temp>temp(?:orary)?\s+)?table\s+"
    r"(?:if\s+not\s+exists\s+)?(?P<name>[\w.\"`]+)\s+as\b",
    re.I,
)
_INSERT_RE = re.compile(
    r"\binsert\s+(?:into|overwrite\s+table)\s+(?P<name>[\w.\"`]+)", re.I
)
_SOURCE_RE = re.compile(r"\b(?:from|join)\s+(?P<name>[\w.\"`]+)", re.I)
_CTE_RE = re.compile(r"(?:\bwith|,)\s*(?P<name>[\w.\"`]+)\s+as\s*\(", re.I)


def _normalize(name):
    """库.表归一化：去引号/反引号、小写、保留库名前缀。"""
    parts = [p.strip().strip('`"') for p in str(name).strip().rstrip(";").split(".")]
    return ".".join(part.lower() for part in parts if part)


def _parse_sql(sql):
    """逐语句解析，返回 [(target 或 None, 是否临时, [来源表]), ...]。"""
    text = _COMMENT_RE.sub(" ", sql)
    statements = []
    for stmt in text.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        ctes = {_normalize(m.group("name")) for m in _CTE_RE.finditer(stmt)}
        sources = []
        for m in _SOURCE_RE.finditer(stmt):
            name = _normalize(m.group("name"))
            if name and name not in ctes and name not in sources:
                sources.append(name)
        target = None
        temp = False
        m = _CREATE_RE.search(stmt)
        if m:
            target = _normalize(m.group("name"))
            temp = bool(m.group("temp"))
        else:
            m = _INSERT_RE.search(stmt)
            if m:
                target = _normalize(m.group("name"))
        statements.append((target, temp, sources))
    return statements


def _renames(spec):
    if not spec:
        return []
    if isinstance(spec, dict):
        return list(spec.items())
    return [tuple(pair) for pair in spec]


class ImpactResult(list):
    """impact 返回值：按层级顺序展开的任务名列表，levels 为分层视图。"""

    def __init__(self, jobs, levels):
        super().__init__(jobs)
        self.levels = levels


class Lineage:
    def __init__(self):
        self.jobs = {}
        self.nodes = {}
        self.edges = []
        self._alias = {}
        self.records = self.jobs

    def record(self, job):
        name = job["name"]
        for old, new in _renames(job.get("renames")):
            self.rename(old, new)
        declared_inputs = [self._canon(t) for t in job.get("inputs", [])]
        declared_outputs = [self._canon(t) for t in job.get("outputs", [])]
        created, loose = self._parse_job_sql(job.get("sql"))

        used_as_source = set()
        for info in created.values():
            used_as_source.update(info["sources"])
        temps = {
            t
            for t, info in created.items()
            if info["temp"] or (t in used_as_source and t not in declared_outputs)
        }

        outputs = list(declared_outputs)
        for t in created:
            if t not in temps and t not in outputs:
                outputs.append(t)

        def external_sources(target, seen=None):
            seen = seen if seen is not None else set()
            result = []
            for src in created.get(target, {}).get("sources", []):
                if src in temps:
                    if src not in seen:
                        seen.add(src)
                        result.extend(external_sources(src, seen))
                elif src not in result:
                    result.append(src)
            return result

        inputs = list(declared_inputs)
        pairs = []
        for out in outputs:
            for src in external_sources(out):
                if src not in inputs:
                    inputs.append(src)
                pairs.append((src, out))
        for src in declared_inputs + loose:
            if src not in inputs:
                inputs.append(src)
            for out in outputs:
                pairs.append((src, out))

        item = {"inputs": inputs, "outputs": outputs, "temps": sorted(temps)}
        self.jobs[name] = item
        self.edges = [e for e in self.edges if e["job"] != name]
        for t in inputs + outputs:
            self.nodes.setdefault(t, {"name": t, "aliases": []})
        seen_pairs = set()
        for src, dst in pairs:
            if src in temps or dst in temps or (src, dst) in seen_pairs:
                continue
            seen_pairs.add((src, dst))
            self.edges.append({"src": src, "dst": dst, "job": name})
        return item

    def rename(self, old, new):
        """表改名：新名为规范名，旧名保留为历史别名，已有边同步改写。"""
        old_c = self._canon(old)
        new_c = self._canon(new)
        if old_c == new_c:
            return new_c
        node = self.nodes.pop(old_c, {"name": old_c, "aliases": []})
        aliases = [a for a in node["aliases"] if a != new_c]
        aliases.append(old_c)
        existing = self.nodes.get(new_c, {"name": new_c, "aliases": []})
        merged = existing["aliases"] + [
            a for a in aliases if a not in existing["aliases"]
        ]
        self.nodes[new_c] = {"name": new_c, "aliases": merged}
        for a in merged:
            self._alias[a] = new_c
        self._alias[old_c] = new_c
        self._alias.pop(new_c, None)
        for edge in self.edges:
            if edge["src"] == old_c:
                edge["src"] = new_c
            if edge["dst"] == old_c:
                edge["dst"] = new_c
        for item in self.jobs.values():
            item["inputs"] = [new_c if t == old_c else t for t in item["inputs"]]
            item["outputs"] = [new_c if t == old_c else t for t in item["outputs"]]
        return new_c

    def impact(self, table, direction="downstream"):
        """按方向从 table 出发多跳展开，返回受影响任务名（分层顺序）。"""
        frontier = {self._canon(table)}
        visited = set(frontier)
        jobs = []
        levels = []
        while frontier:
            level = []
            next_frontier = set()
            for edge in self.edges:
                if direction == "upstream":
                    hit, nxt = edge["dst"] in frontier, edge["src"]
                else:
                    hit, nxt = edge["src"] in frontier, edge["dst"]
                if not hit:
                    continue
                if edge["job"] not in jobs and edge["job"] not in level:
                    level.append(edge["job"])
                if nxt not in visited:
                    next_frontier.add(nxt)
            if level:
                levels.append(level)
                jobs.extend(level)
            visited |= next_frontier
            frontier = next_frontier
        return ImpactResult(jobs, levels)

    def _canon(self, name):
        norm = _normalize(name)
        return self._alias.get(norm, norm)

    @staticmethod
    def _parse_job_sql(sql):
        if not sql:
            return {}, []
        if isinstance(sql, (list, tuple)):
            sql = ";\n".join(sql)
        created = {}
        loose = []
        for target, temp, sources in _parse_sql(sql):
            if target is None:
                for s in sources:
                    if s not in loose:
                        loose.append(s)
                continue
            info = created.setdefault(target, {"temp": False, "sources": []})
            info["temp"] = info["temp"] or temp
            for s in sources:
                if s not in info["sources"]:
                    info["sources"].append(s)
        return created, loose
