"""子域枚举结果的收敛与归并。

一轮候选按解析结果收敛成入口清单：

* 指向同一地址的名字合并成一条入口，全部名字保留在 ``names`` 里；
* 泛解析命中的名字单独列出，保留原始解析记录，可单独恢复成有效入口；
* 解析失败与解析超时分开记录，已确认无效的候选在后续轮次直接跳过；
* 每轮结果按域名和轮次分开存放，可以只比较新增部分。

泛解析地址按以下顺序认定：

1. 候选行显式标记 ``status="wildcard"`` 或 ``wildcard=True``；
2. 名字形如 ``*.example.com`` 的探测行，或 ``load(..., wildcard_ips=[...])``
   传入的地址；
3. 同一地址在本轮命中不少于 ``threshold`` 个不同名字、且覆盖本轮已解析
   名字的 ``WILDCARD_RATIO`` 以上时，按扫描规模推断为泛解析地址。

推断只是兜底，门槛可逐域名配置（``wildcard_threshold``），设为 0 关闭。
误判的记录用 ``restore(domain, name, ip=None)`` 单独恢复。
"""

import copy
import ipaddress

# 推断泛解析的默认门槛：同一地址至少命中多少个不同名字。
# 显式标记（status="wildcard" / 探测行 / wildcard_ips）优先，这里只是兜底，
# 门槛和工作量占比都取得比较严，避免把同一台反向代理后面的正常站点
# 误判成泛解析；需要时用 wildcard_threshold 逐域名调整。
DEFAULT_WILDCARD_THRESHOLD = 20
# 推断泛解析时，该地址还要覆盖本轮已解析名字的这个比例以上。
WILDCARD_RATIO = 0.8

_FAIL_STATUSES = {
    "fail", "failed", "failure", "dead", "nxdomain", "nx", "nonexistent",
    "notfound", "not_found", "invalid", "error", "no", "rejected", "blocked",
}
_TIMEOUT_STATUSES = {"timeout", "timedout", "timed_out", "time-out", "time out"}
_WILDCARD_STATUSES = {"wildcard", "wild", "wildcard_ip", "泛解析", "泛域名"}

# 同一名字的多条记录合并时，按“更有结论”的方向取状态。
_KIND_RANK = {"fail": 1, "timeout": 2, "ok": 3, "wildcard": 4}

_IP_KEYS = ("ips", "ip", "addresses", "address", "a", "addrs")

# 域名 -> 全部轮次、已确认无效的名字、泛解析地址与人工恢复记录。
_DOMAINS = {}


def _truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "通配"}


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _normalize_name(raw):
    """域名归一：去空白、转小写、去结尾的点；空值返回空串。"""
    if raw is None:
        return ""
    return str(raw).strip().lower().rstrip(".")


def _normalize_ip(raw):
    """地址归一；无法识别时返回 None，由调用方决定是否报错。"""
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _row_ips(row):
    """取候选行的全部地址；单个 ip、ips 列表、逗号/空格分隔都认。

    多个地址键同时出现时并起来取；空值不算数，``{"ips": [], "ip": ...}``
    这种只填了部分字段的行也能拿到地址。
    """
    raw = []
    for key in _IP_KEYS:
        if key in row:
            raw.extend(_as_list(row[key]))
    ips = []
    for item in raw:
        for part in str(item).replace(",", " ").replace("，", " ").split():
            ip = _normalize_ip(part)
            if ip is not None and ip not in ips:
                ips.append(ip)
    return ips


def _row_kind(row, ips):
    """把一行候选归类为 probe / wildcard / ok / timeout / fail。"""
    if _truthy(row.get("probe")) or str(row.get("name", "")).strip().startswith("*."):
        return "probe"
    if _truthy(row.get("wildcard")):
        return "wildcard"
    status = row.get("status", "ok")
    text = str(status).strip().lower() if status is not None else ""
    if text in _WILDCARD_STATUSES:
        return "wildcard"
    if text in _TIMEOUT_STATUSES:
        return "timeout"
    if text in _FAIL_STATUSES:
        return "fail"
    if ips:
        return "ok"
    # 状态是 ok 却没有地址的候选不构成入口，按解析失败处理。
    return "fail"


def _blank_domain():
    return {
        "rounds": [],
        "confirmed_invalid": {},
        "wildcard_ips": set(),
        # 人工恢复过的名字：后续轮次不再按通配或无效处理，直到 reset。
        "restored": {},
        "threshold": DEFAULT_WILDCARD_THRESHOLD,
    }


def _state(domain):
    state = _DOMAINS.get(domain)
    if state is None or not state["rounds"]:
        raise ValueError("域名未载入")
    return state


def _round_of(state, number):
    if number is None:
        return state["rounds"][-1]
    if isinstance(number, bool) or not isinstance(number, int):
        raise ValueError("轮次必须是整数")
    if number < 1 or number > len(state["rounds"]):
        raise ValueError("轮次不存在")
    return state["rounds"][number - 1]


def load(domain, rows, wildcard_ips=None, wildcard_threshold=None):
    """登记一轮枚举候选，按解析结果收敛后按轮次保存。

    ``wildcard_ips`` 是事先探到的泛解析地址，对后续轮次同样生效；
    ``wildcard_threshold`` 调整按规模推断泛解析的门槛，0 表示不推断。
    同一个域名反复调用就是多轮枚举，轮次从 1 开始递增。

    已确认无效的名字不会二次消耗解析：本次传入的行里如果带着同名候选，
    返回值 ``skipped`` 里照样列出，``enumerate`` / ``report`` 也不重复上报。
    整轮校验通过后才写状态，中途报错不会留下半轮数据。
    """
    if not isinstance(domain, str) or not domain:
        raise ValueError("domain 不能为空")
    if not isinstance(rows, list):
        raise ValueError("rows 必须是列表")

    new_wildcard_ips = set()
    for item in _as_list(wildcard_ips):
        ip = _normalize_ip(item)
        if ip is None:
            raise ValueError("wildcard_ips 含非法地址: %r" % (item,))
        new_wildcard_ips.add(ip)
    if wildcard_threshold is not None:
        if isinstance(wildcard_threshold, bool) or not isinstance(wildcard_threshold, int):
            raise ValueError("wildcard_threshold 必须是整数")
        if wildcard_threshold < 0:
            raise ValueError("wildcard_threshold 不能为负")

    records = []
    probes = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("候选必须是字典")
        name = _normalize_name(row.get("name"))
        if not name:
            raise ValueError("候选缺少名字")
        ips = _row_ips(row)
        kind = _row_kind(row, ips)
        if kind == "probe":
            # 探测行本身不是入口，只用来确认泛解析地址。
            new_wildcard_ips.update(ips)
            probes.append({
                "name": name,
                "ips": ips,
                "status": str(row.get("status", "ok")),
                "raw": copy.deepcopy(row),
            })
            continue
        if kind == "wildcard":
            new_wildcard_ips.update(ips)
        records.append({
            "name": name,
            "ips": ips,
            "kind": kind,
            "status": str(row.get("status", "ok")),
            "raw": copy.deepcopy(row),
        })

    state = _DOMAINS.setdefault(domain, _blank_domain())
    if wildcard_threshold is not None:
        state["threshold"] = wildcard_threshold
    # 显式确认的泛解析地址跨轮次保留；按规模推断的只对本轮生效，
    # 免得一次误判把后续轮次里的正常名字一直压成通配。
    state["wildcard_ips"].update(new_wildcard_ips)
    effective_ips = set(state["wildcard_ips"])
    effective_ips.update(_infer_wildcard_ips(records, state["threshold"]))

    number = len(state["rounds"]) + 1
    buckets = _bucket_records(records, state, number, effective_ips)
    state["rounds"].append({
        "number": number,
        "candidates": copy.deepcopy(rows),
        "probes": probes,
        "wildcard_ips": sorted(effective_ips),
        "resolved": buckets["resolved"],
        "wildcard": buckets["wildcard"],
        "failed": buckets["failed"],
        "timeout": buckets["timeout"],
        "skipped": buckets["skipped"],
        "restored": {},
    })
    return {"domain": domain, "loaded": len(rows), "round": number,
            "skipped": sorted(buckets["skipped"])}


def _infer_wildcard_ips(records, threshold):
    """按扫描规模推断泛解析地址：命中的名字多且占比高的地址。"""
    if threshold < 2:
        return set()
    names_by_ip = {}
    ok_names = set()
    for record in records:
        if record["kind"] != "ok" or not record["ips"]:
            continue
        ok_names.add(record["name"])
        for ip in record["ips"]:
            names_by_ip.setdefault(ip, set()).add(record["name"])
    if not ok_names:
        return set()
    inferred = set()
    for ip, names in names_by_ip.items():
        if len(names) >= threshold and len(names) / len(ok_names) >= WILDCARD_RATIO:
            inferred.add(ip)
    return inferred


def _merge_records(records):
    """同一名字的多条记录合并，地址取并集，状态取更接近结论的一条。"""
    merged = {}
    for record in records:
        item = merged.get(record["name"])
        if item is None:
            merged[record["name"]] = {
                "name": record["name"],
                "ips": list(record["ips"]),
                "kind": record["kind"],
                "records": [record],
            }
            continue
        for ip in record["ips"]:
            if ip not in item["ips"]:
                item["ips"].append(ip)
        if _KIND_RANK[record["kind"]] > _KIND_RANK[item["kind"]]:
            item["kind"] = record["kind"]
        item["records"].append(record)
    return merged


def _bucket_item(item):
    """收敛后的名字条目：最近一条的状态 + 该名字的全部原始记录。"""
    record = item["records"][-1]
    return {
        "name": item["name"],
        "ips": list(item["ips"]),
        "status": record["status"],
        "records": [
            {"name": r["name"], "ips": list(r["ips"]), "status": r["status"],
             "raw": copy.deepcopy(r["raw"])}
            for r in item["records"]
        ],
    }


def _wildcard_item(item):
    """通配命中条目：保留原始解析记录，事后可据此核对是否误判。"""
    entry = _bucket_item(item)
    entry["raw"] = copy.deepcopy(item["records"][-1]["raw"])
    return entry


def _bucket_records(records, state, number, wildcard_ips):
    """把归一后的记录分到有效 / 通配 / 失败 / 超时 / 跳过五个桶。"""
    merged = _merge_records(records)
    confirmed = state["confirmed_invalid"]
    restored = state["restored"]

    resolved, wildcard, failed, timeout, skipped = {}, {}, {}, {}, {}

    for name in sorted(merged):
        item = merged[name]
        ips = item["ips"]
        kind = item["kind"]

        if name in restored:
            # 人工恢复过的名字永久不再按通配或无效处理：本轮解析到地址就
            # 用本轮地址，解析不到才用恢复时登记的地址。
            use = list(ips) or list(restored[name]["ips"])
            if use:
                entry = _bucket_item(item)
                entry["ips"] = use
                entry["restored"] = True
                resolved[name] = entry
                confirmed.pop(name, None)
                continue

        if kind in ("ok", "wildcard"):
            if not ips:
                kind = "fail"
            elif kind == "wildcard" or set(ips) <= wildcard_ips:
                wildcard[name] = _wildcard_item(item)
                # 能解析出地址就不再是“已确认无效”。
                confirmed.pop(name, None)
                continue
            else:
                resolved[name] = _bucket_item(item)
                confirmed.pop(name, None)
                continue

        if kind == "timeout":
            # 之前已确认无效的名字不再重复上报；首次超时只记为超时。
            if name in confirmed:
                skipped[name] = _bucket_item(item)
            else:
                timeout[name] = _bucket_item(item)
            continue

        if name in confirmed:
            skipped[name] = _bucket_item(item)
        else:
            failed[name] = _bucket_item(item)
            confirmed[name] = {
                "name": name,
                "status": item["records"][-1]["status"],
                "ips": list(ips),
                "round": number,
                "raw": copy.deepcopy(item["records"][-1]["raw"]),
            }

    return {
        "resolved": resolved,
        "wildcard": wildcard,
        "failed": failed,
        "timeout": timeout,
        "skipped": skipped,
    }


def _entries(record):
    """把指向同一地址的名字并成一条入口，保留全部名字。

    地址做并查集：一个名字解析到多个地址时这些地址连成一组，
    只要两个名字共享其中任一地址就算同一条入口。这样顺序无关，
    不会因为先读到哪个地址而把同一条入口拆开。
    """
    parent = {}

    def find(ip):
        parent.setdefault(ip, ip)
        root = ip
        while parent[root] != root:
            root = parent[root]
        while parent[ip] != root:
            parent[ip], ip = root, parent[ip]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            # 固定的合并方向，保证结果与输入顺序无关。
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    for item in record["resolved"].values():
        ips = item["ips"]
        for ip in ips[1:]:
            union(ips[0], ip)
        for ip in ips:
            find(ip)

    groups = {}
    for name in sorted(record["resolved"]):
        item = record["resolved"][name]
        if not item["ips"]:
            continue
        groups.setdefault(find(item["ips"][0]), []).append(name)

    entries = []
    for root in sorted(groups):
        names = sorted(groups[root])
        ips = sorted(ip for ip in parent if find(ip) == root)
        entries.append({
            "name": names[0],
            "names": names,
            "ip": root,
            "ips": ips,
            "alias_count": len(names) - 1,
            "statuses": sorted({record["resolved"][n]["status"] for n in names}),
            "records": [{"name": n, "ips": list(record["resolved"][n]["ips"])}
                        for n in names],
        })
    return entries


def _wildcard_records(record):
    return [copy.deepcopy(record["wildcard"][name])
            for name in sorted(record["wildcard"])]


def _round_names(record):
    """本轮出现过的全部名字，含被跳过的已确认无效项。"""
    names = set(record["resolved"])
    names.update(record["wildcard"])
    names.update(record["failed"])
    names.update(record["timeout"])
    names.update(record["skipped"])
    return names


def _counts(record):
    entries = _entries(record)
    names = len(_round_names(record))
    valid = len(entries)
    return {
        "round": record["number"],
        "candidates": len(record["candidates"]),
        "probes": len(record["probes"]),
        "names": names,
        "total": valid,
        "valid": valid,
        "aliases": sum(entry["alias_count"] for entry in entries),
        "wildcard": len(record["wildcard"]),
        "failed": len(record["failed"]),
        "timeout": len(record["timeout"]),
        "invalid": len(record["failed"]) + len(record["timeout"]),
        "skipped": len(record["skipped"]),
        "coverage": round(valid / names, 3) if names else 0.0,
    }


def enumerate(domain, round=None, only_new=False):
    """按域名取一轮枚举结果：有效入口、别名、通配命中和无效数量。

    ``round`` 缺省取最新一轮；``only_new=True`` 时各项只统计相对之前轮次
    新增的名字，已确认过的记录不会再报一遍。
    """
    state = _state(domain)
    record = _round_of(state, round)
    entries = _entries(record)
    wildcards = _wildcard_records(record)
    counts = _counts(record)
    aliases = counts["aliases"]
    invalid = counts["invalid"]
    new_names = []

    if only_new:
        seen = set()
        for earlier in state["rounds"][:record["number"] - 1]:
            seen |= _round_names(earlier)
        new_names = sorted(_round_names(record) - seen)
        new_set = set(new_names)
        kept = []
        for entry in entries:
            fresh = [name for name in entry["names"] if name in new_set]
            if fresh:
                kept.append(dict(entry, new_names=fresh))
        entries = kept
        wildcards = [item for item in wildcards if item["name"] in new_set]
        aliases = sum(len(entry["names"]) - 1 for entry in entries)
        invalid = sum(
            1 for name in list(record["failed"]) + list(record["timeout"])
            if name in new_set
        )

    return {
        "domain": domain,
        "round": record["number"],
        "entries": entries,
        "total": len(entries),
        "aliases": aliases,
        "wildcard": len(wildcards),
        "wildcard_names": sorted(item["name"] for item in wildcards),
        "wildcard_records": wildcards,
        "invalid": invalid,
        "failed": counts["failed"],
        "timeout": counts["timeout"],
        "skipped": counts["skipped"],
        "names": counts["names"],
        "candidates": counts["candidates"],
        "new_names": new_names,
    }


def _pct(value):
    """百分比格式化，避免和 report() 的 round 参数重名。"""
    return float("%.1f" % (100.0 * value))


def report(domain, round=None):
    """按域名汇总真实覆盖：入口数按归并后的条数算，不按候选条数。"""
    state = _state(domain)
    record = _round_of(state, round)
    counts = _counts(record)
    return {
        "domain": domain,
        "round": counts["round"],
        "candidates": counts["candidates"],
        "probes": counts["probes"],
        "names": counts["names"],
        # 真实覆盖：归并后的入口数，而不是候选条数。
        "total": counts["valid"],
        "valid": counts["valid"],
        "aliases": counts["aliases"],
        "wildcard": counts["wildcard"],
        "failed": counts["failed"],
        "timeout": counts["timeout"],
        "invalid": counts["invalid"],
        "skipped": counts["skipped"],
        "coverage": counts["coverage"],
        "coverage_percent": _pct(counts["coverage"]),
        "entries": _entries(record),
        "wildcard_names": sorted(record["wildcard"]),
        "wildcard_records": _wildcard_records(record),
        "wildcard_ips": list(record["wildcard_ips"]),
        "failed_names": sorted(record["failed"]),
        "timeout_names": sorted(record["timeout"]),
        "skipped_names": sorted(record["skipped"]),
        "restored": _restored_audit(state),
        "rounds": [_counts(item) for item in state["rounds"]],
    }


def _restored_audit(state):
    records = []
    for record in state["rounds"]:
        for name in sorted(record["restored"]):
            item = dict(record["restored"][name])
            item["round"] = record["number"]
            records.append(item)
    return records


def diff(domain, old=None, new=None):
    """比较两轮结果，只给新增部分，已确认过的记录不重复上报。"""
    state = _state(domain)
    current = _round_of(state, new)
    old_number = current["number"] - 1 if old is None else old
    if isinstance(old_number, bool) or not isinstance(old_number, int):
        raise ValueError("轮次必须是整数")
    if old_number < 1:
        raise ValueError("没有上一轮可比较")
    previous = _round_of(state, old_number)

    seen = set(_round_names(previous))
    for earlier in state["rounds"][:current["number"] - 1]:
        seen |= _round_names(earlier)
    added = sorted(_round_names(current) - seen)
    removed = sorted(_round_names(previous) - _round_names(current))
    added_set = set(added)
    return {
        "domain": domain,
        "old_round": old_number,
        "new_round": current["number"],
        "added": len(added),
        "added_names": added,
        "removed": len(removed),
        "removed_names": removed,
        "added_entries": [
            dict(entry, new_names=[n for n in entry["names"] if n in added_set])
            for entry in _entries(current)
            if added_set.intersection(entry["names"])
        ],
        "added_wildcard": sorted(n for n in current["wildcard"] if n in added_set),
        "added_invalid": sorted(
            n for n in list(current["failed"]) + list(current["timeout"])
            if n in added_set
        ),
    }


def restore(domain, name, ip=None):
    """恢复一条被判为通配或无效的记录，恢复后按有效入口计。

    通配命中用原始解析地址恢复；失败记录可以用 ``ip`` 补上地址。
    两者都拿不到地址时只解除“已确认无效”，让下一轮重新解析，
    不凭空造出一条入口。

    先在最新一轮里找；最近一轮没出现这个名字时，从更早的轮次里找，
    恢复记录算在最新一轮上（运营是在最新一轮看到它需要恢复的）。
    """
    state = _state(domain)
    key = _normalize_name(name)
    if not key:
        raise ValueError("name 不能为空")
    normalized = None
    if ip is not None:
        normalized = _normalize_ip(ip)
        if normalized is None:
            raise ValueError("ip 不是合法地址: %r" % (ip,))

    record = state["rounds"][-1]
    if key in record["resolved"]:
        return {"domain": domain, "name": key, "restored": False,
                "reason": "already_valid", "pending": False}

    bucket_names = ("wildcard", "failed", "timeout", "skipped")
    source = None
    item = None
    found_in = None
    for candidate in reversed(state["rounds"]):
        for bucket_name in bucket_names:
            if key in candidate[bucket_name]:
                source = bucket_name
                item = candidate[bucket_name][key]
                found_in = candidate
                break
        if item is not None:
            break

    if item is None:
        if state["confirmed_invalid"].pop(key, None) is not None:
            # 之前已确认无效、本轮没再出现：解除确认，下一轮重新解析。
            return {"domain": domain, "name": key, "restored": False,
                    "reason": "confirmed_cleared", "pending": True}
        return {"domain": domain, "name": key, "restored": False,
                "reason": "not_found", "pending": False}

    ips = [normalized] if normalized is not None else list(item["ips"])
    state["confirmed_invalid"].pop(key, None)
    if not ips:
        # 没有地址可用：只解除无效判定，记录留在原桶里等下一轮重新解析。
        audit = {
            "name": key, "from": source, "round": record["number"],
            "ips": [], "pending": True,
        }
        record["restored"][key] = audit
        return {"domain": domain, "name": key, "restored": False,
                "from": source, "ips": [], "pending": True,
                "round": record["number"]}

    if found_in is record:
        # 本轮里的记录直接改判；更早轮次是历史，只在其基础上修正，不删改。
        del found_in[source][key]
    record["resolved"][key] = {
        "name": key,
        "ips": ips,
        "status": item["status"],
        "restored": True,
        "records": item["records"],
    }
    # 记录到域名级：后续轮次继续把这个名字当有效入口，直到 reset。
    state["restored"][key] = {"ips": ips, "round": record["number"],
                              "from": source}
    record["restored"][key] = {
        "name": key, "from": source, "round": record["number"],
        "ips": list(ips), "pending": False,
    }
    return {"domain": domain, "name": key, "restored": True, "from": source,
            "ips": list(ips), "pending": False, "round": record["number"]}


def reset(domain):
    """丢弃某域名的全部轮次与已确认状态，下一轮从头枚举。"""
    if not isinstance(domain, str) or not domain:
        raise ValueError("domain 不能为空")
    _DOMAINS.pop(domain, None)
    return {"domain": domain, "reset": True}
