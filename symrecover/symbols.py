"""带证据的二进制符号恢复。

三类匹配信号，各自留下依据和可信程度：
1. 已知库特征（魔数/特征字符串/调用特征）—— catalog.py 与 binary 自带目录；
2. 公开符号表（binary["symbols"]，等同开源版本的权威映射，直接 high）；
3. 调用关系——一个未识别函数的调用方/被调方都已恢复时，按目录里的
   调用特征做定点传播；无法唯一定位的保持未恢复，不硬猜。

匹配不上的函数保留 sub_<地址> 名字，并按模块分组交给人工分批处理。
每个函数有一个与地址无关的稳定标识 stable_id：由调用结构、字符串、
常量等内容特征做颜色求精（Weisfeiler-Lehman 思路）算出，同一函数换
版本、换地址、甚至改名后标识不变。人工固定和改名都进历史，可追溯。
"""

import hashlib

from .catalog import DEFAULT_CATALOG

_REGISTRY = {}          # (binary name, version) -> 该版本最近一次 recover 的完整结果
_IDENTITIES = {}        # stable_id -> 身份档案（当前名、改名历史、人工固定）
_PINS = {}              # (binary, address) -> 人工固定记录
_USER_CATALOG = []      # load_catalog 追加的目录条目

_ADDRESS_PREFIX = "sub_"
_ID_PREFIX = "FID-"
_WL_ROUNDS = 3

# 可信程度排序：未恢复 < 调用关系推断 < 单条库特征 < 多信号一致 < 权威符号表 < 人工固定
_CONFIDENCE_RANK = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "manual": 4,
}
_RANK_CONFIDENCE = {v: k for k, v in _CONFIDENCE_RANK.items()}


# ---------------------------------------------------------------- 基础工具

def _norm_address(value):
    """地址统一成小写无前缀十六进制串，便于跨输入对齐。"""
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text


def _norm_constants(values):
    out = []
    for value in values or []:
        if isinstance(value, str):
            text = value.strip().lower()
            if text.startswith("0x"):
                text = text[2:]
            try:
                value = int(text, 16) if text else 0
            except ValueError:
                continue
        out.append(int(value) & 0xFFFFFFFFFFFFFFFF)
    return out


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _hash12(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12].upper()


def _all_catalog(binary):
    """内置目录 + 全局限定目录 + 该二进制自带目录，后写的不覆盖先写的。"""
    merged = {}
    for entry in list(DEFAULT_CATALOG) + list(_USER_CATALOG) + _as_list(binary.get("catalog")):
        name = entry.get("name")
        if not name or name in merged:
            continue
        merged[name] = {
            "name": name,
            "library": entry.get("library", ""),
            "constants": set(_norm_constants(entry.get("constants"))),
            "strings": set(entry.get("strings") or []),
            "callees": set(entry.get("callees") or []),
        }
    return merged


# ---------------------------------------------------------------- 证据模型

def _evidence(signal, confidence, detail, name=None, library=""):
    return {
        "signal": signal,          # symbol/constant/string/callee/callgraph/manual
        "name": name,              # 该信号指向的函数名，冲突时用来对比
        "library": library,
        "confidence": confidence,
        "detail": detail,          # 人能看懂、能回溯到原始数据的依据
    }


def _best(evidence_list):
    """返回 (最高级别, {指向名字: 该名字的证据})，没证据返回 (0, {})。"""
    if not evidence_list:
        return 0, {}
    rank = max(_CONFIDENCE_RANK[e["confidence"]] for e in evidence_list)
    top = {}
    for ev in evidence_list:
        if _CONFIDENCE_RANK[ev["confidence"]] == rank and ev.get("name"):
            top.setdefault(ev["name"], []).append(ev)
    return rank, top


def _finalize_evidence(evidence_list):
    """从全部候选证据里定结论：同级只指向一个名字才成立，否则记冲突不命名。

    高一级信号单独压过低级信号（符号表 > 库特征 > 调用关系），只有同
    级别里出现两个不同名字才算冲突，冲突双方都留在依据里交人工裁决。
    两个及以上不同信号指向同一名字时，可信程度上调一级（封顶 high）。
    """
    rank, top = _best(evidence_list)
    if rank == 0 or not top:
        return None, "none", evidence_list
    if len(top) > 1:
        names = sorted(top)
        conflict = _evidence(
            "conflict", _RANK_CONFIDENCE[rank],
            "同级信号指向不同候选 %s，未自动命名" % " / ".join(names),
        )
        return None, "none", evidence_list + [conflict]

    (name, own), = top.items()
    signals = {ev["signal"] for ev in own}
    confidence = _RANK_CONFIDENCE[rank]
    if len(signals) >= 2 and rank < _CONFIDENCE_RANK["high"]:
        confidence = _RANK_CONFIDENCE[rank + 1]
    return name, confidence, evidence_list


# ---------------------------------------------------------------- 匹配引擎

def _index_functions(binary):
    funcs = {}
    for raw in binary.get("functions") or []:
        address = _norm_address(raw.get("address", ""))
        if not address or address in funcs:
            continue
        funcs[address] = {
            "address": address,
            "module": str(raw.get("module") or binary.get("name") or "unknown"),
            "size": int(raw.get("size") or 0),
            "calls": [_norm_address(c) for c in raw.get("calls") or []],
            "strings": [str(s) for s in raw.get("strings") or []],
            "constants": _norm_constants(raw.get("constants")),
            "imports": [str(s) for s in raw.get("imports") or []],
        }
    return funcs


def _public_symbols(binary):
    """公开符号表：地址 -> 名字，权威映射，high 可信度。"""
    table = {}
    for item in _as_list(binary.get("symbols")):
        if isinstance(item, dict):
            address = _norm_address(item.get("address", ""))
            name = item.get("name")
        else:
            # 也接受 ["name=0xADDR", ...] 这种紧凑写法
            text = str(item)
            if "=" not in text:
                continue
            name, _, addr = text.partition("=")
            address, name = _norm_address(addr), name.strip()
        if address and name:
            table[address] = str(name)
    return table


def _collect_direct_evidence(funcs, symbols, catalog):
    """每个函数先收集不依赖其他函数结论的直接证据。"""
    # 导入桩自身就是该外部函数的入口，导入表给它真实名字（high）。
    import_names = {}
    for addr, func in funcs.items():
        if len(func["imports"]) == 1 and not func["calls"]:
            import_names[addr] = func["imports"][0]

    direct = {addr: [] for addr in funcs}
    for addr, func in funcs.items():
        if addr in symbols:
            name = symbols[addr]
            direct[addr].append(_evidence(
                "symbol", "high",
                "公开符号表映射 %s -> %s" % (addr, name),
                name=name, library=catalog.get(name, {}).get("library", ""),
            ))
        if addr in import_names:
            name = import_names[addr]
            direct[addr].append(_evidence(
                "symbol", "high",
                "导入表真实名字 %s" % name,
                name=name, library=catalog.get(name, {}).get("library", ""),
            ))

        const_set = set(func["constants"])
        str_set = set(func["strings"])
        const_hits = []
        str_hits = []
        for entry in catalog.values():
            if entry["constants"] and entry["constants"].issubset(const_set):
                const_hits.append(entry)
            if entry["strings"] and entry["strings"].issubset(str_set):
                str_hits.append(entry)

        # 同一信号命中多条时，要求特征最多的条目（最具体的签名）胜出；
        # 特征数相同又指向不同名字，才作为同级别冲突留给人工裁决。
        def _specific(hits, kind, feature_key):
            if not hits:
                return
            best_size = max(len(e[feature_key]) for e in hits)
            winners = [e for e in hits if len(e[feature_key]) == best_size]
            names = {e["name"] for e in winners}
            if len(names) > 1:
                for entry in winners:
                    direct[addr].append(_evidence(
                        kind, "high" if kind == "constant" else "medium",
                        "同级 %s 特征撞车：%s 同样具体，未自动选择"
                        % (kind, entry["name"]),
                        name=entry["name"], library=entry["library"],
                    ))
                return
            entry = winners[0]
            matched = sorted(entry[feature_key])
            if kind == "constant":
                detail = "库特征匹配（%s）：常量 %s" % (
                    entry["library"] or "未知库", ", ".join(hex(c) for c in matched))
            else:
                detail = "库特征匹配（%s）：字符串 %s" % (
                    entry["library"] or "未知库", ", ".join(matched))
            if len(hits) > 1:
                others = sorted(e["name"] for e in hits if e["name"] != entry["name"])
                detail += "；%s 的特征是其子集，更具体签名优先" % "、".join(others)
            direct[addr].append(_evidence(
                kind, "high" if kind == "constant" else "medium", detail,
                name=entry["name"], library=entry["library"],
            ))

        _specific(const_hits, "constant", "constants")
        _specific(str_hits, "string", "strings")
        # imports 的命名作用只体现在纯导入桩上（见上方 import_names）；
        # 普通函数的 imports 仅作为调用特征，在传播阶段供 callees 匹配。
    return direct


def _callee_evidence(func, funcs, resolved, catalog, import_names):
    """调用特征：目录条目要求的被调函数全部已恢复且名字一致。

    被调名字既可能来自本地函数的恢复结论，也可能来自导入表里的真实名字。
    只考虑没有常量/字符串这类更强直接特征的条目，避免调用特征给已经被
    魔数签名定名的函数堆无关依据。
    """
    found = []
    for callee_addr in func["calls"]:
        if callee_addr in resolved:
            found.append(resolved[callee_addr])
        elif callee_addr in import_names:
            found.append(import_names[callee_addr])
    found_set = set(found)
    const_set = set(func["constants"])
    str_set = set(func["strings"])
    results = []
    for entry in catalog.values():
        if not entry["callees"]:
            continue
        # 条目自身带常量/字符串特征却没在本函数命中，说明它不是本函数，
        # 只是调用形状恰好相似，不能拿调用关系硬套。
        if entry["constants"] and not entry["constants"].issubset(const_set):
            continue
        if entry["strings"] and not entry["strings"].issubset(str_set):
            continue
        if entry["callees"].issubset(found_set):
            results.append(_evidence(
                "callee", "medium",
                "调用特征匹配（%s）：已恢复被调函数 %s"
                % (entry["library"] or "未知库", ", ".join(sorted(entry["callees"]))),
                name=entry["name"], library=entry["library"],
            ))
    return results


def _propagate_callgraph(funcs, evidence, catalog, import_names):
    """定点传播调用关系证据，直到没有新结论。

    导入表名字先入已解析集合，本地恢复出的名字随后逐轮并入。
    传播本身是结构性推断，证据级别 low；它与直接证据同级竞争、共同
    参与多信号升级，不会盖过任何库特征或符号表。
    """
    # 先把只靠直接证据（符号表/常量/字符串/导入桩）就能定名的函数全部
    # 解析掉，它们是调用关系传播的起点。
    resolved = dict(import_names)
    for addr in funcs:
        if addr in resolved:
            continue
        name, _, _ = _finalize_evidence(evidence[addr])
        if name:
            resolved[addr] = name

    # 第 0 轮：被调函数此刻全部可命名，命中的调用特征是直接证据（medium）。
    # 之后每一轮都依赖上一轮新恢复的函数，属于传播推断，只给 low。
    for round_index in range(max(len(funcs), 1)):
        progressed = False
        # 本轮证据生成前已定名的函数：它们再命中调用特征属于多信号互证，
        # 给 medium；本轮还没名字的函数，调用特征是传播推断，给 low。
        named_before = set(resolved)
        for addr, func in funcs.items():
            # 证据生成对所有函数都要跑：已有直接证据的函数也可能再命中
            # 调用特征，多个独立信号一致时可信度才能上调。
            for ev in _callee_evidence(func, funcs, resolved, catalog, import_names):
                # 同一候选的调用类证据每个函数只留一条：传播结论（low）
                # 一旦在某轮成立，后续轮次不再升级，保持可信度稳定。
                if any(
                    old.get("name") == ev["name"]
                    and old["signal"] in ("callgraph", "callee")
                    for old in evidence[addr]
                ):
                    continue
                if addr not in named_before:
                    prop = dict(ev)
                    prop["signal"] = "callgraph"
                    prop["confidence"] = "low"
                    prop["detail"] = "调用关系传播：" + ev["detail"]
                    evidence[addr].append(prop)
                else:
                    evidence[addr].append(ev)
                progressed = True
        changed = False
        for addr in funcs:
            if addr in resolved:
                continue
            name, _, _ = _finalize_evidence(evidence[addr])
            if name:
                resolved[addr] = name
                changed = True
        if not changed and not progressed:
            break
    return resolved


# ---------------------------------------------------------------- 稳定标识

def _structural_colors(funcs):
    """按内容特征做 WL 颜色求精：同结构的函数颜色相同，结构变了颜色才变。

    名字和地址都不进颜色输入（邻居用的是邻居自身的结构色而非地址或
    恢复名），所以改名、换版本地址漂移都不影响标识。
    """
    def seed(func):
        payload = "|".join([
            func["module"],
            str(func["size"]),
            ",".join(sorted(func["strings"])),
            ",".join(hex(c) for c in sorted(func["constants"])),
            # 调用数量和外部导入构成调用形状，但不依赖任何地址或名字
            "calls=%d" % len(func["calls"]),
            ",".join(sorted(func["imports"])),
        ])
        return _hash12(payload)

    colors = {addr: seed(func) for addr, func in funcs.items()}
    for _ in range(_WL_ROUNDS):
        updated = {}
        for addr, func in funcs.items():
            neighbor = ",".join(sorted(colors.get(c, "ext") for c in func["calls"]))
            updated[addr] = _hash12(colors[addr] + ">" + neighbor)
        colors = updated
    return colors


def _assign_stable_ids(funcs, colors):
    """同颜色类按地址确定序拆分，保证一次 recover 内标识唯一且可复现。"""
    classes = {}
    for addr in sorted(funcs):
        classes.setdefault((funcs[addr]["module"], colors[addr]), []).append(addr)
    ids = {}
    for (module, color), members in classes.items():
        for index, addr in enumerate(sorted(members)):
            suffix = "" if len(members) == 1 else ("-%d" % (index + 1))
            ids[addr] = _ID_PREFIX + color + suffix
    return ids


# ---------------------------------------------------------------- 身份与历史

def _touch_identity(stable_id, name, module, source, detail, confidence, pinned=False):
    """登记/更新身份档案；名字变化才写历史，同名重复出现不产生噪音。"""
    record = _IDENTITIES.get(stable_id)
    if record is None:
        record = {
            "stable_id": stable_id,
            "module": module,
            "current_name": name,
            "pinned": pinned,
            "history": [{
                "name": name,
                "source": source,
                "confidence": confidence,
                "detail": detail,
            }],
        }
        _IDENTITIES[stable_id] = record
        return record
    record["module"] = module
    if name != record["current_name"]:
        record["history"].append({
            "name": name,
            "source": source,
            "confidence": confidence,
            "detail": detail,
        })
        record["current_name"] = name
    if pinned:
        record["pinned"] = True
    return record


# ---------------------------------------------------------------- 主入口

def recover(binary):
    """对一个二进制做带证据的符号恢复。

    返回恢复数量、每个函数的结论（名字/依据/可信度/稳定标识）、未恢复
    地址（含按模块的分组与数量）。同名二进制的不同版本分别留存以便跨
    版本对照；身份档案和人工固定记录跨 recover 保留。
    """
    if not isinstance(binary, dict):
        raise ValueError("binary must be a dict")
    if not isinstance(binary.get("functions"), list):
        raise ValueError("functions must be a list")

    binary_name = str(binary.get("name", ""))
    version = str(binary.get("version", ""))
    funcs = _index_functions(binary)
    symbols = _public_symbols(binary)
    catalog = _all_catalog(binary)

    evidence = _collect_direct_evidence(funcs, symbols, catalog)
    import_names = {
        addr: func["imports"][0]
        for addr, func in funcs.items()
        if len(func["imports"]) == 1 and not func["calls"]
    }
    _propagate_callgraph(funcs, evidence, catalog, import_names)

    decisions = {}
    for addr in funcs:
        name, confidence, all_ev = _finalize_evidence(evidence[addr])
        decisions[addr] = (name, confidence, all_ev)

    colors = _structural_colors(funcs)
    stable_ids = _assign_stable_ids(funcs, colors)

    entries = []
    unmatched = []
    for addr in sorted(funcs):
        func = funcs[addr]
        name, confidence, all_ev = decisions[addr]
        stable_id = stable_ids[addr]
        pin = _PINS.get((binary_name, addr))

        if pin is not None:
            # 人工固定优先于一切自动结论，原结论保留在依据里可回溯
            all_ev = all_ev + [_evidence(
                "manual", "manual",
                "人工固定：%s（%s）" % (pin["name"], pin.get("note", "无备注")),
                name=pin["name"],
            )]
            name, confidence = pin["name"], "manual"
            _touch_identity(stable_id, name, func["module"], "manual",
                            pin.get("note", "人工固定"), "manual", pinned=True)
        elif name is not None:
            _touch_identity(stable_id, name, func["module"], "recover",
                            version or binary_name, confidence)
        else:
            sub_name = _ADDRESS_PREFIX + addr
            _touch_identity(stable_id, sub_name, func["module"], "unmatched",
                            "无充分证据，保留地址名", "none")

        entry = {
            "address": addr,
            "module": func["module"],
            "name": name if name is not None else _ADDRESS_PREFIX + addr,
            "recovered": name is not None or pin is not None,
            "confidence": confidence,
            "evidence": all_ev,
            "stable_id": stable_id,
            "history": list(_IDENTITIES[stable_id]["history"]),
        }
        entries.append(entry)
        if not entry["recovered"]:
            unmatched.append({
                "address": addr,
                "module": func["module"],
                "name": entry["name"],
                "stable_id": stable_id,
                "evidence": all_ev,
            })

    grouped = {}
    for item in unmatched:
        grouped.setdefault(item["module"], []).append(item["address"])
    for module in grouped:
        grouped[module].sort()

    result = {
        "binary": binary_name,
        "version": version,
        "recovered": sum(1 for e in entries if e["recovered"]),
        "total": len(entries),
        "names": entries,
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
        "unmatched_by_module": grouped,
    }
    # 同名二进制的不同版本各自保留，跨版本对照才拿得到两边的地址
    _REGISTRY[(binary_name, version)] = result
    return result


def _records_of(binary):
    """取一个二进制的全部版本记录；也兼容直接传 (name, version) 键。"""
    if isinstance(binary, tuple):
        record = _REGISTRY.get(binary)
        return [record] if record is not None else []
    return [record for key, record in _REGISTRY.items() if key[0] == binary]


def lookup(binary, address):
    """按二进制+地址取一条函数结论，取不到返回空字典。

    同一二进制有多个版本时取最新 recover 的那条记录。
    """
    target = _norm_address(address)
    for record in reversed(_records_of(binary)):
        for item in record["names"]:
            if item["address"] == target:
                return dict(item)
    return {}


def pin(binary, address, name, note=""):
    """人工固定一个函数的名字，低可信结论经人工确认后固化下来。

    固定记录按二进制名+地址挂账，跨 recover 保留，并写入该稳定标识的
    改名历史；已 recover 的结果立即回写生效，未 recover 的地址在下次
    recover 时自动应用。
    """
    addr = _norm_address(address)
    _PINS[(binary, addr)] = {"name": name, "note": note}
    stable_id = ""
    for record in _records_of(binary):
        for item in record["names"]:
            if item["address"] != addr:
                continue
            stable_id = item["stable_id"]
            _touch_identity(stable_id, name, item["module"], "manual",
                            note or "人工固定", "manual", pinned=True)
            # 立即回写缓存结果，分析同学不用重新 recover 就能看到结论
            if not any(ev["signal"] == "manual" for ev in item["evidence"]):
                item["evidence"].append(_evidence(
                    "manual", "manual",
                    "人工固定：%s（%s）" % (name, note or "无备注"),
                    name=name,
                ))
            item["name"] = name
            item["confidence"] = "manual"
            item["history"] = list(_IDENTITIES[stable_id]["history"])
            if not item["recovered"]:
                item["recovered"] = True
                record["recovered"] += 1
                record["unmatched_count"] -= 1
                record["unmatched"] = [
                    u for u in record["unmatched"] if u["address"] != addr
                ]
                module = item["module"]
                record["unmatched_by_module"].setdefault(module, [])
                record["unmatched_by_module"][module] = [
                    a for a in record["unmatched_by_module"][module] if a != addr
                ]
    return {
        "binary": binary,
        "address": addr,
        "name": name,
        "stable_id": stable_id,
        "pinned": True,
    }


def identity(stable_id):
    """按稳定标识查看身份档案（当前名字、改名历史、是否人工固定）。"""
    record = _IDENTITIES.get(stable_id)
    if record is None:
        return {}
    return dict(record)


def export(format="json", binary=None):
    """按函数导出恢复结果，能和公开符号表对上。

    - json: 完整函数记录（含依据、可信程度、稳定标识、历史）；
    - sym:  地址 + 名字的符号表文本，可直接给反编译器导入；
    - id:   稳定标识 <-> 名字 <-> 各版本地址的对照，供跨版本追踪。
    """
    results = []
    if binary is not None:
        results = _records_of(binary)
    else:
        results = [_REGISTRY[key] for key in sorted(_REGISTRY)]

    if format == "json":
        return [
            {
                "binary": record["binary"],
                "version": record["version"],
                "functions": [dict(item) for item in record["names"]],
            }
            for record in results
        ]

    if format == "sym":
        lines = []
        for record in results:
            lines.append("# %s %s" % (record["binary"], record["version"]))
            for item in record["names"]:
                if item["recovered"]:
                    lines.append("0x%s %s" % (item["address"], item["name"]))
        return "\n".join(lines)

    if format == "id":
        mapping = {}
        for record in results:
            for item in record["names"]:
                slot = mapping.setdefault(item["stable_id"], {
                    "stable_id": item["stable_id"],
                    "name": item["name"],
                    "addresses": [],
                })
                slot["addresses"].append({
                    "binary": record["binary"],
                    "version": record["version"],
                    "address": item["address"],
                })
        return [mapping[key] for key in sorted(mapping)]

    raise ValueError("format must be one of json/sym/id")


def report():
    """全局汇总：恢复数量、可信分布、依据清单、未恢复地址分组与数量。

    同名二进制的多个版本分别计数；binaries 是不同二进制名的数量，
    versions 是注册表中恢复记录（二进制×版本）的数量。
    """
    confidence_dist = {}
    evidence_rows = []
    unmatched_modules = {}
    total = recovered = unmatched_count = 0
    binary_names = set()

    for key in sorted(_REGISTRY):
        record = _REGISTRY[key]
        binary_name = key[0]
        binary_names.add(binary_name)
        total += record["total"]
        recovered += record["recovered"]
        unmatched_count += record["unmatched_count"]
        for module, addrs in record["unmatched_by_module"].items():
            bucket = unmatched_modules.setdefault((binary_name, module), [])
            bucket.extend(addrs)
        for item in record["names"]:
            confidence_dist[item["confidence"]] = \
                confidence_dist.get(item["confidence"], 0) + 1
            for ev in item["evidence"]:
                evidence_rows.append({
                    "binary": binary_name,
                    "version": record["version"],
                    "address": item["address"],
                    "stable_id": item["stable_id"],
                    "name": item["name"],
                    "signal": ev["signal"],
                    "evidence_name": ev.get("name"),
                    "confidence": ev["confidence"],
                    "detail": ev["detail"],
                })

    return {
        "binaries": len(binary_names),
        "versions": len(_REGISTRY),
        "total": total,
        "recovered": recovered,
        "confidence": confidence_dist,
        "evidence": evidence_rows,
        "unmatched": [
            {
                "binary": binary_name,
                "module": module,
                "addresses": sorted(addrs),
                "count": len(addrs),
            }
            for (binary_name, module), addrs in sorted(unmatched_modules.items())
        ],
        "unmatched_count": unmatched_count,
    }


def load_catalog(entries):
    """追加分析同学自己整理的库特征目录，返回当前全局目录条目数。"""
    for entry in _as_list(entries):
        if isinstance(entry, dict) and entry.get("name"):
            _USER_CATALOG.append(dict(entry))
    return len(DEFAULT_CATALOG) + len(_USER_CATALOG)


def reset_catalog():
    """清掉 load_catalog 追加的条目，恢复内置目录。"""
    _USER_CATALOG.clear()
    return len(DEFAULT_CATALOG)


def _reset_state():
    """测试辅助：清空全部进程内状态。"""
    _REGISTRY.clear()
    _IDENTITIES.clear()
    _PINS.clear()
    _USER_CATALOG.clear()
