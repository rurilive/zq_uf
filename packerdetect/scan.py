"""按结构和行为识别加壳样本，并对脱壳前后做逐项比对。

识别信号分四类，区段名只是其中一类，改名后其余三类仍然成立：

1. 区段名（弱依赖，可被改名绕过）；
2. 区段结构：RWX、可写空白解压区、高熵、区段数量等；
3. 入口点位置：是否落在最后一个区段/是否不在常规可执行区段；
4. 导入表与运行时行为：最小导入集、动态解析、自修改/反调试等。

判定结论分三种：
- 命中已知家族（packer=家族名，pending=False），依据全部写进 evidence；
- 结构/行为表明有壳但对不上家族（packer="unknown"，pending=True），
  structural 里保留结构特征供人工判断；
- 没有加壳信号（packer="unpacked"，pending=False）。
"""

from .signatures import (
    FAMILY_BEHAVIORS,
    FAMILY_SECTION_NAMES,
    FAMILY_STRUCTURAL_RULES,
    HIGH_ENTROPY,
    LOADER_IMPORTS,
    MINIMAL_IMPORT_FAMILIES,
    STRUCTURAL_MATCH_SCORE,
    STRUCTURAL_WEIGHTS,
)

_EXEC_CHARS = frozenset(["x", "rx", "rwx", "exec", "execute"])
_WRITE_CHARS = frozenset(["w", "rw", "rwx", "write", "writable"])


def _parse_address(value):
    """把 0x401000 / "401000" / 4198400 统一成整数；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return int(text, 16) if text.startswith("0x") else int(text, 0)
    except ValueError:
        return None


def _normalize_section(section):
    """区段可以是字符串，也可以是带属性的字典；统一成字典。

    字典会复制一份再归一化，绝不在调用方的入参上写字段。
    """
    if isinstance(section, str):
        return {"name": section}
    if isinstance(section, dict) and section.get("name") is not None:
        return dict(section)
    raise ValueError("区段必须是字符串或带 name 的字典")


def _normalize_sections(sections):
    if not isinstance(sections, list) or not sections:
        raise ValueError("样本缺少区段信息")
    normalized = [_normalize_section(item) for item in sections]
    for index, section in enumerate(normalized):
        section["name"] = str(section["name"]).lower()
        section.setdefault("index", index)
        chars = section.get("characteristics") or section.get("chars") or section.get("perms") or ""
        section["_chars"] = str(chars).lower().replace(" ", "")
        section["_vsize"] = section.get("virtual_size", section.get("vsize"))
        section["_rsize"] = section.get("raw_size", section.get("rsize"))
        section["_entropy"] = section.get("entropy")
        section["_vaddr"] = _parse_address(section.get("virtual_address") or section.get("vaddr"))
    return normalized


def _entry_section_index(sections, entry):
    if entry is None:
        return None
    for section in sections:
        base = section["_vaddr"]
        if base is None or section["_vsize"] is None:
            continue
        try:
            if base <= entry < base + int(section["_vsize"]):
                return section["index"]
        except (TypeError, ValueError):
            continue
    return None


def extract_features(sample):
    """从样本字典提取与区段名无关的结构特征，detect 和人工复核共用。"""
    sections = _normalize_sections(sample.get("sections"))
    entry = _parse_address(sample.get("entry"))
    entry_idx = _entry_section_index(sections, entry)
    last_idx = len(sections) - 1

    names = [section["name"] for section in sections]
    behavior = [str(item).lower() for item in sample.get("behavior", [])]
    imports = [str(item).lower() for item in sample.get("imports", [])]

    high_entropy = [
        section["name"]
        for section in sections
        if isinstance(section["_entropy"], (int, float)) and section["_entropy"] >= HIGH_ENTROPY
    ]
    rwx_sections = [section["name"] for section in sections if section["_chars"] == "rwx"]
    writable_zero_raw = [
        section["name"]
        for section in sections
        if section["_chars"] in _WRITE_CHARS and section["_rsize"] == 0
    ]

    exec_section_indices = {
        section["index"] for section in sections if section["_chars"] in _EXEC_CHARS
    }
    normal_exec = [idx for idx in exec_section_indices if idx != last_idx]

    return {
        "section_names": names,
        "section_count": len(sections),
        "entry": entry,
        "entry_section": names[entry_idx] if entry_idx is not None else None,
        "entry_in_last_section": entry_idx == last_idx if entry_idx is not None else False,
        "stolen_entry": bool(
            entry_idx is not None and normal_exec and entry_idx not in normal_exec
        ),
        "rwx_sections": rwx_sections,
        "writable_zero_raw_sections": writable_zero_raw,
        "high_entropy_sections": high_entropy,
        "minimal_imports": bool(imports) and all(name in LOADER_IMPORTS for name in imports),
        "import_count": len(imports),
        "behaviors": behavior,
    }


def _family_name_hits(features):
    """区段名命中：每个命中都是强证据。"""
    hits = {}
    for family, aliases in FAMILY_SECTION_NAMES.items():
        matched = [name for name in features["section_names"] if name in aliases]
        if matched:
            hits[family] = matched
    return hits


def _family_structure_hits(features):
    """结构规则命中：不依赖区段名，改名样本靠这里认家族。"""
    values = {
        "writable_zero_raw": bool(features["writable_zero_raw_sections"]),
        "entry_in_last_section": features["entry_in_last_section"],
        "minimal_imports": features["minimal_imports"],
        "high_entropy_section": bool(features["high_entropy_sections"]),
        "rwx_section": bool(features["rwx_sections"]),
        "stolen_entry": features["stolen_entry"],
    }
    results = {}
    for family, rules in FAMILY_STRUCTURAL_RULES.items():
        matched = [rule for rule, required in rules.items() if required and values.get(rule)]
        score = sum(STRUCTURAL_WEIGHTS[rule] for rule in matched)
        if matched:
            results[family] = {"rules": matched, "score": score}
    return results


def _generic_packing_signals(features):
    """跨家族的通用加壳信号，对不上家族时用来判定 unknown 并支撑人工分析。"""
    signals = []
    if features["rwx_sections"]:
        signals.append(("rwx_section", "存在可读写可执行(RWX)区段: %s" % ",".join(features["rwx_sections"])))
    if features["writable_zero_raw_sections"]:
        signals.append(("writable_zero_raw", "存在可写且原始大小为0的空白解压区: %s"
                        % ",".join(features["writable_zero_raw_sections"])))
    if features["high_entropy_sections"]:
        signals.append(("high_entropy", "存在高熵(>=%s)区段: %s"
                        % (HIGH_ENTROPY, ",".join(features["high_entropy_sections"]))))
    if features["entry_in_last_section"]:
        signals.append(("entry_in_last_section", "入口点落在最后一个区段 %s" % features["entry_section"]))
    if features["stolen_entry"]:
        signals.append(("stolen_entry", "入口点不在常规可执行区段，疑似 stolen bytes"))
    if features["minimal_imports"]:
        signals.append(("minimal_imports", "导入表只剩加载器API(LoadLibrary/GetProcAddress)"))
    dynamic = [name for name in features["behaviors"]
               if name in ("alloc_rwx", "self_modify", "dynamic_api_resolve", "process_hollow")]
    if dynamic:
        signals.append(("runtime_behavior", "运行时加壳行为: %s" % ",".join(dynamic)))
    return signals


def detect(sample):
    """按结构和行为判定样本是否加壳，结论附全部判定依据。"""
    if not isinstance(sample, dict):
        raise ValueError("sample 必须是字典")
    features = extract_features(sample)

    name_hits = _family_name_hits(features)
    structure_hits = _family_structure_hits(features)
    behavior_set = set(features["behaviors"])
    signals = _generic_packing_signals(features)

    # 1) 区段名命中 -> 高置信已知家族；再补上同家族的结构/行为依据
    if name_hits:
        family = sorted(name_hits, key=lambda key: (-len(name_hits[key]), key))[0]
        evidence = ["section:%s" % name for name in name_hits[family]]
        if family in structure_hits:
            evidence.extend("structure:%s" % rule for rule in structure_hits[family]["rules"])
        behavior_match = [name for name in FAMILY_BEHAVIORS.get(family, []) if name in behavior_set]
        evidence.extend("behavior:%s" % name for name in behavior_match)
        return _result(sample, family, "high", evidence, features, False, None)

    # 2) 无区段名：结构分够阈值 -> 中置信已知家族（改名样本走这里）
    candidates = sorted(
        ((family, info) for family, info in structure_hits.items()
         if info["score"] >= STRUCTURAL_MATCH_SCORE),
        key=lambda item: (-item[1]["score"], item[0]),
    )
    if candidates:
        family, info = candidates[0]
        evidence = ["structure:%s" % rule for rule in info["rules"]]
        evidence.append("structure_match:score=%s,threshold=%s" % (info["score"], STRUCTURAL_MATCH_SCORE))
        behavior_match = [name for name in FAMILY_BEHAVIORS.get(family, []) if name in behavior_set]
        evidence.extend("behavior:%s" % name for name in behavior_match)
        note = "无区段名命中，由结构特征判定为 %s；建议人工复核" % family
        return _result(sample, family, "medium", evidence, features, False, note)

    # 3) 对不上家族：通用信号 >=2 判 unknown 待人工；单个弱信号同样待人工但不下壳结论
    if len(signals) >= 2:
        evidence = ["signal:%s|%s" % (kind, detail) for kind, detail in signals]
        note = "结构/行为显示有壳但对不上已知家族，待人工判断"
        return _result(sample, "unknown", "low", evidence, features, True, note)
    if signals:
        evidence = ["signal:%s|%s" % (kind, detail) for kind, detail in signals]
        note = "仅出现单一弱信号，证据不足，待人工确认"
        return _result(sample, "unknown", "low", evidence, features, True, note)

    return _result(sample, "unpacked", "high", [], features, False, None)


def _result(sample, packer, confidence, evidence, features, pending, note):
    return {
        "sample": sample.get("name", ""),
        "packer": packer,
        "confidence": confidence,
        "evidence": evidence,
        "structural": _structural_summary(features),
        "pending": pending,
        "pending_reason": note,
        "unpacked": None,
    }


def _structural_summary(features):
    """unknown 样本留给人工的结构画像。"""
    return {
        "section_count": features["section_count"],
        "entry_section": features["entry_section"],
        "entry_in_last_section": features["entry_in_last_section"],
        "stolen_entry": features["stolen_entry"],
        "rwx_sections": features["rwx_sections"],
        "writable_zero_raw_sections": features["writable_zero_raw_sections"],
        "high_entropy_sections": features["high_entropy_sections"],
        "minimal_imports": features["minimal_imports"],
        "import_count": features["import_count"],
        "behaviors": features["behaviors"],
    }


def diff(before, after):
    """脱壳前后逐项比对：区段、入口点、导入表、结构特征、行为。"""
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("样本必须是字典")

    before_sections = _normalize_sections(before.get("sections", [])) if before.get("sections") else []
    after_sections = _normalize_sections(after.get("sections", [])) if after.get("sections") else []
    old_names = [section["name"] for section in before_sections]
    new_names = [section["name"] for section in after_sections]

    section_added = [name for name in new_names if name not in old_names]
    section_removed = [name for name in old_names if name not in new_names]

    entry_before = _parse_address(before.get("entry"))
    entry_after = _parse_address(after.get("entry"))
    if entry_before is None and entry_after is None:
        entry_changed = False
    else:
        entry_changed = entry_before != entry_after

    old_imports = [str(name).lower() for name in before.get("imports", [])]
    new_imports = [str(name).lower() for name in after.get("imports", [])]
    imports_added = [name for name in new_imports if name not in old_imports]
    imports_removed = [name for name in old_imports if name not in new_imports]
    import_delta = (
        ["import_added:%s" % name for name in imports_added]
        + ["import_removed:%s" % name for name in imports_removed]
    )

    before_behavior = {str(name).lower() for name in before.get("behavior", [])}
    after_behavior = {str(name).lower() for name in after.get("behavior", [])}

    # 脱壳后重新跑一遍识别，用结果判断脱得干不干净
    recheck = detect(after) if after_sections else None
    clean = recheck is not None and recheck["packer"] == "unpacked" and not recheck["pending"]

    return {
        "sample": after.get("name") or before.get("name", ""),
        "section_added": section_added,
        "section_removed": section_removed,
        "entry_changed": entry_changed,
        "entry_before": ("0x%x" % entry_before) if entry_before is not None else None,
        "entry_after": ("0x%x" % entry_after) if entry_after is not None else None,
        "imports_added": imports_added,
        "imports_removed": imports_removed,
        "import_delta": import_delta,
        "behavior_added": sorted(after_behavior - before_behavior),
        "behavior_removed": sorted(before_behavior - after_behavior),
        "recheck": recheck,
        "clean": clean,
        "changed": bool(
            section_added or section_removed or entry_changed
            or import_delta or before_behavior != after_behavior
        ),
    }


def families():
    """已知家族及其区段名特征（保持原入口，结构规则见 signatures）。"""
    return {family: list(names) for family, names in FAMILY_SECTION_NAMES.items()}
