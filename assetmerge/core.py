"""按主机合并资产，合并后按主机下发扫描，支持预览与拆分。

合并依据：
  * host            —— 资产记录显式标注属于同一台主机（host 相同）。
  * resolved-ip     —— 域名解析到的地址，与某条 ip 记录的地址相同
                       （解析结果由资产记录的可选字段 ``resolves_to`` 给出，
                       支持单个字符串或字符串列表）。
  * port-instance   —— 端口记录 ``ip:port`` 中的地址，与同地址的 ip 记录
                       属于同一实例。

任意一条依据都通过并查集把记录合入同一个主机分组；分组依据取该分组内
实际发生过的全部依据。合并后同一漏洞只保留一条，并列出全部受影响入口。
"""

# ---------------------------------------------------------------------------
# 模块状态
#
# _ORIGINALS：归一化后的原始记录，任何操作都不允许丢失（守恒不变量）。
# _GROUPS：    当前生效的主机分组。每条原始记录在任意时刻恰好属于一个分组；
#              未参与合并的记录自成单条分组，因此原始记录总数始终守恒。
# _LAST_MERGE：最近一次 merge 产生的分组结构，用于拆分时回退到合并前原样。
# ---------------------------------------------------------------------------
_ORIGINALS = []
_GROUPS = []
_LAST_MERGE = []


class MergeAbort(ValueError):
    """执行结果与预览不一致时抛出；差异挂在 ``differences`` 上，状态保持不变。"""

    def __init__(self, message, differences):
        super().__init__(message)
        self.differences = differences


# ---------------------------------------------------------------------------
# 并查集
# ---------------------------------------------------------------------------
class _DSU:
    def __init__(self):
        self.parent = {}
        # 根节点 -> 该连通块内发生过的合并依据集合
        self.basis = {}

    def add(self, item):
        if item not in self.parent:
            self.parent[item] = item
            self.basis[item] = set()

    def find(self, item):
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            nxt = self.parent[item]
            self.parent[item] = root
            item = nxt
        return root

    def union(self, a, b, basis):
        """把 a、b 合入同一连通块，并登记该次合并的依据。"""
        self.add(a)
        self.add(b)
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            # 即使已经连通，该依据也是这个分组真实成立的一条理由。
            self.basis[ra].add(basis)
            return
        merged = self.basis[ra] | self.basis[rb] | {basis}
        self.parent[rb] = ra
        self.basis[ra] = merged
        self.basis[rb] = set()


# ---------------------------------------------------------------------------
# 归一化与分组规划（纯函数，不动模块状态）
# ---------------------------------------------------------------------------
def _normalize(assets):
    if not isinstance(assets, list):
        raise ValueError("assets 必须是列表")
    rows = []
    seen_ids = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ValueError("资产记录必须是字典")
        record_id = asset.get("id", "r%d" % index)
        if record_id in seen_ids:
            raise ValueError("资产记录 id 重复: %r" % record_id)
        seen_ids.add(record_id)
        host = asset.get("host") or asset.get("value")
        if not host:
            raise ValueError("资产记录缺少主机标识")
        rows.append({
            "id": record_id,
            "host": host,
            "kind": asset.get("kind", "unknown"),
            "value": asset.get("value", host),
            "vulns": list(asset.get("vulns", [])),
            "source": asset.get("source"),
            # 可选：域名解析结果，字符串或字符串列表。
            "resolves_to": list(asset.get("resolves_to", [])),
        })
    return rows


def _plan_groups(rows):
    """按三类依据做并查集分组，返回分组列表（成员按记录 id 升序）。"""
    dsu = _DSU()
    for row in rows:
        dsu.add(row["id"])

    # 依据 1：显式 host 相同。
    host_members = {}
    for row in rows:
        host_members.setdefault(row["host"], []).append(row["id"])
    for ids in host_members.values():
        for other in ids[1:]:
            dsu.union(ids[0], other, "host")

    # 依据 2：域名解析到的地址与某条 ip 记录相同。
    ip_owners = {row["value"]: row["id"]
                 for row in rows if row["kind"] == "ip"}
    for row in rows:
        for addr in row["resolves_to"]:
            owner = ip_owners.get(addr)
            if owner is not None and owner != row["id"]:
                dsu.union(row["id"], owner, "resolved-ip")

    # 依据 3：端口记录的地址部分与同地址 ip 记录属于同一实例；
    # 地址相同的两条端口记录同样属于同一实例（即使没有独立 ip 记录）。
    port_owners = {}
    for row in rows:
        if row["kind"] != "port" or ":" not in str(row["value"]):
            continue
        addr = str(row["value"]).rsplit(":", 1)[0]
        owner = ip_owners.get(addr) or port_owners.get(addr)
        if owner is not None and owner != row["id"]:
            dsu.union(row["id"], owner, "port-instance")
        port_owners.setdefault(addr, row["id"])

    blocks = {}
    for row in rows:
        blocks.setdefault(dsu.find(row["id"]), []).append(row["id"])

    by_id = {row["id"]: row for row in rows}
    groups = []
    for member_ids in blocks.values():
        member_ids = sorted(member_ids)
        root = dsu.find(member_ids[0])
        basis = sorted(dsu.basis[root]) if len(member_ids) > 1 else []
        groups.append(_build_group(by_id, member_ids, basis))
    return sorted(groups, key=lambda group: group["_members"][0])


def _build_group(by_id, member_ids, basis):
    """把若干原始记录组装成一个主机分组（主记录 + 边上信息）。"""
    members = [by_id[mid] for mid in member_ids]
    primary_id = member_ids[0]
    primary = by_id[primary_id]

    aliases = []
    vuln_entries = {}
    sources = set()
    for row in members:
        if row["source"] is not None:
            sources.add(row["source"])
        for name in row["vulns"]:
            vuln_entries.setdefault(name, [])
            if row["value"] not in vuln_entries[name]:
                vuln_entries[name].append(row["value"])
        if row["id"] == primary_id:
            continue
        aliases.append({
            "id": row["id"],
            "host": row["host"],
            "kind": row["kind"],
            "value": row["value"],
            "source": row["source"],
        })

    vulns = [{"name": name, "entries": vuln_entries[name]}
             for name in sorted(vuln_entries)]
    return {
        "group_id": "g-%s" % primary_id,
        "primary_record": primary_id,
        "host": primary["host"],
        "kind": primary["kind"],
        "value": primary["value"],
        "basis": basis,
        "merged_count": len(member_ids),
        "aliases": aliases,
        "alias_count": len(aliases),
        "vulns": vulns,
        "sources": sorted(sources),
        # 内部字段：成员 id 与每条成员的来源，供拆分与报告使用。
        "_members": list(member_ids),
        "_member_sources": {row["id"]: row["source"] for row in members},
    }


def _public_group(group):
    """对外输出时去掉内部字段。"""
    return {key: value for key, value in group.items()
            if not key.startswith("_")}


def _snapshot(groups):
    return [dict(group,
                 aliases=[dict(alias) for alias in group["aliases"]],
                 vulns=[dict(vuln) for vuln in group["vulns"]],
                 basis=list(group["basis"]),
                 sources=list(group["sources"]),
                 _members=list(group["_members"]),
                 _member_sources=dict(group["_member_sources"]))
            for group in groups]


def _check_conservation(originals, groups):
    """守恒不变量：分组里的原始记录集合必须与资产表完全一致，不多不少。"""
    grouped = sorted(mid for group in groups for mid in group["_members"])
    original = sorted(row["id"] for row in originals)
    if grouped != original:
        raise RuntimeError(
            "资产数量守恒校验失败: 原始 %d 条，分组内 %d 条"
            % (len(original), len(grouped)))


def _summary(originals, groups, splits):
    merged = [g for g in groups if g["merged_count"] > 1]
    return {
        "count": len(originals),          # 原始资产记录总数（守恒口径）
        "group_count": len(groups),       # 合并后的主机数量
        "merged_group_count": len(merged),
        "affected_count": sum(g["merged_count"] for g in merged),
        "alias_count": sum(g["alias_count"] for g in groups),
        "basis": "host" if merged else "record",
        "groups": [_public_group(g) for g in groups],
        "splits": list(splits),
    }


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------
def preview(assets):
    """只做合并预演，不改变当前状态。返回受影响记录条数与预演结果。"""
    rows = _normalize(assets)
    planned = _plan_groups(rows)
    result = _summary(rows, planned, [])
    result["dry_run"] = True
    return result


def merge(assets, expect=None):
    """按主机合并资产表并生效。

    merge(assets) 的调用方式与原接口保持一致；传入 ``expect``（通常为
    preview 的返回）时，先比对执行结果与预览，不一致则中止并在异常的
    ``differences`` 中列出差异，模块状态保持不变。
    """
    rows = _normalize(assets)
    planned = _plan_groups(rows)
    _check_conservation(rows, planned)

    if expect is not None:
        planned_summary = _summary(rows, planned, [])
        diffs = _diff_expect(expect, planned_summary)
        if diffs:
            raise MergeAbort("合并结果与预览不一致，已中止", diffs)

    _ORIGINALS[:] = rows
    _GROUPS[:] = _snapshot(planned)
    _LAST_MERGE[:] = _snapshot(planned)
    return _summary(rows, _GROUPS, [])


def _diff_expect(expect, actual):
    """比对预览与实际执行结果，返回人类可读的差异列表（空列表表示一致）。"""
    diffs = []
    fields = ("count", "group_count", "merged_group_count",
              "affected_count", "alias_count", "basis")
    for field in fields:
        if field in expect and expect.get(field) != actual.get(field):
            diffs.append({"field": field,
                          "expect": expect.get(field),
                          "actual": actual.get(field)})

    expect_groups = expect.get("groups")
    if isinstance(expect_groups, list):
        actual_groups = {g["group_id"]: g for g in actual["groups"]}
        expect_ids = {g.get("group_id") for g in expect_groups
                      if isinstance(g, dict)}
        actual_ids = set(actual_groups)
        for gid in sorted(expect_ids - actual_ids):
            diffs.append({"field": "groups", "group_id": gid,
                          "expect": "分组存在", "actual": "分组缺失"})
        for gid in sorted(actual_ids - expect_ids):
            diffs.append({"field": "groups", "group_id": gid,
                          "expect": "分组不存在", "actual": "分组多出"})
        for g in expect_groups:
            if not isinstance(g, dict):
                continue
            gid = g.get("group_id")
            if gid not in actual_groups:
                continue
            actual_group = actual_groups[gid]
            for field in ("primary_record", "merged_count",
                          "alias_count", "basis"):
                if field in g and g.get(field) != actual_group.get(field):
                    diffs.append({"field": "groups.%s.%s" % (gid, field),
                                  "expect": g.get(field),
                                  "actual": actual_group.get(field)})
    return diffs


def split(group_id=None, record_id=None):
    """只回退指定的一次合并，其余分组保持原样。

    可按合并分组 id（``g-<主记录id>``）或组内任意记录 id 指定；
    单条记录的分组无需拆分。拆分后该分组的原始记录恢复为独立分组，
    并在返回的 ``splits`` 中记录回退结果。
    """
    if not _GROUPS:
        raise ValueError("当前没有可拆分的合并结果，请先调用 merge")
    if group_id is None and record_id is None:
        raise ValueError("split 需要指定 group_id 或 record_id")
    if group_id is not None and record_id is not None:
        raise ValueError("group_id 与 record_id 只能指定一个")

    target = None
    if group_id is not None:
        for group in _GROUPS:
            if group["group_id"] == group_id:
                target = group
                break
        if target is None:
            raise ValueError("找不到合并分组: %r" % group_id)
    else:
        for group in _GROUPS:
            if record_id in group["_members"]:
                target = group
                break
        if target is None:
            raise ValueError("记录不属于任何分组: %r" % record_id)

    if target["merged_count"] == 1:
        raise ValueError("分组 %s 只有一条记录，无需拆分" % target["group_id"])

    before_ids = sorted(mid for g in _GROUPS for mid in g["_members"])
    restored = list(target["_members"])

    new_groups = [g for g in _GROUPS if g is not target]
    by_id = {row["id"]: row for row in _ORIGINALS}
    for mid in restored:
        new_groups.append(_build_group(by_id, [mid], []))
    new_groups = sorted(new_groups, key=lambda g: g["_members"][0])

    # 拆分只动一个分组，其余分组对象保持原样；总数必须守恒。
    _check_conservation(_ORIGINALS, new_groups)
    after_ids = sorted(mid for g in new_groups for mid in g["_members"])
    if after_ids != before_ids:
        raise RuntimeError("拆分前后资产集合不一致，已拒绝执行")

    _GROUPS[:] = _snapshot(new_groups)

    split_record = {
        "group_id": target["group_id"],
        "primary_record": target["primary_record"],
        "restored_records": restored,
        "restored_count": len(restored),
        "basis": list(target["basis"]),
    }
    return _summary(_ORIGINALS, _GROUPS, [split_record])


def report():
    """按主机汇总：主机数量、资产数量、别名数量与漏洞分布（同漏洞只算一条）。"""
    vuln_hosts = {}      # 漏洞 -> 出现在哪些主机分组
    vuln_entries = {}    # 漏洞 -> 全部受影响入口（去重）
    host_distribution = []
    for group in _GROUPS:
        host_distribution.append({
            "group_id": group["group_id"],
            "host": group["host"],
            "primary_record": group["primary_record"],
            "asset_count": group["merged_count"],
            "alias_count": group["alias_count"],
            "vuln_count": len(group["vulns"]),
            "vulns": [item["name"] for item in group["vulns"]],
        })
        for item in group["vulns"]:
            name = item["name"]
            vuln_hosts.setdefault(name, set()).add(group["group_id"])
            bucket = vuln_entries.setdefault(name, [])
            for entry in item["entries"]:
                if entry not in bucket:
                    bucket.append(entry)

    vulns = {name: {"host_count": len(vuln_hosts[name]),
                    "hosts": sorted(vuln_hosts[name]),
                    "entries": vuln_entries[name]}
             for name in sorted(vuln_hosts)}
    return {
        "hosts": len(_GROUPS),
        "records": len(_ORIGINALS),
        "alias_count": sum(g["alias_count"] for g in _GROUPS),
        "basis": "host" if any(g["merged_count"] > 1 for g in _GROUPS)
                 else "record",
        "vulns": vulns,
        "vuln_total": len(vulns),
        "host_distribution": host_distribution,
    }


def scan_list():
    """按主机返回下发清单：每个主机一个条目（主记录 id），按 id 升序。"""
    return sorted((group["primary_record"] for group in _GROUPS),
                  key=lambda record_id: record_id)
