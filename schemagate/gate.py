"""结构变更门禁：破坏性变更阻断，兼容变更按表灰度，失败自动回滚。

接口保持 check(old, new) 和 apply(plan)：
- check：对比新旧结构，破坏性变更（删列、收窄类型）阻断并给迁移建议，
  兼容变更（加列、放宽类型）放行并留痕；结果可经 export 字段导出 JSON。
- apply：破坏性变更直接阻断；兼容变更按 gray_ratio 对表灰度，灰度期间
  新旧结构并存、读写兼容，成功后放量全量；灰度失败自动回滚到上一版本。
  审批人、原因和变更摘要写入审计。
"""

import json
import time

# 类型兼容序：只能变更为能容纳旧值的类型（int -> float -> text）
_TYPE_ORDER = {"int": 1, "float": 2, "text": 3}

_version = 0         # 全局结构版本号，单调递增
_versions = {}       # 表 -> 当前结构版本
_history = {}        # 表 -> 历史版本栈，回滚恢复到上一版本
_audit = []          # 全量审计留痕


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _next_version():
    global _version
    _version += 1
    return _version


def _bump(table, version):
    _history.setdefault(table, []).append(_versions.get(table, 0))
    _versions[table] = version


def _rollback(table):
    history = _history.get(table) or []
    if history:
        _versions[table] = history.pop()


def _type_compatible(old, new):
    if old == new:
        return True
    if old in _TYPE_ORDER and new in _TYPE_ORDER:
        return _TYPE_ORDER[new] >= _TYPE_ORDER[old]
    return False


def _record(entry):
    _audit.append(entry)
    return entry


def check(old, new):
    """对比新旧结构，返回门禁结果。破坏性变更阻断并附迁移建议。"""
    old = old or {}
    new = new or {}
    breaking, compatible = [], []
    for col, t in old.items():
        if col not in new:
            breaking.append({
                "column": col, "kind": "drop_column", "from": t, "to": None,
                "suggestion": "先新增同义列双写并回填，确认下游全部迁移后再下线该列",
            })
        elif not _type_compatible(t, new[col]):
            breaking.append({
                "column": col, "kind": "narrow_type", "from": t, "to": new[col],
                "suggestion": "新建 %s 类型列并回填历史数据，读取切换后再下线旧列" % new[col],
            })
        elif t != new[col]:
            compatible.append({"column": col, "kind": "widen_type", "from": t, "to": new[col]})
    for col, t in new.items():
        if col not in old:
            compatible.append({"column": col, "kind": "add_column", "from": None, "to": t})

    parts = ["%s列 %s" % ({"add_column": "新增", "widen_type": "放宽",
                          "drop_column": "删除", "narrow_type": "收窄"}[c["kind"]], c["column"])
             for c in compatible + breaking]
    summary = "；".join(parts) if parts else "无结构变更"
    version = _next_version()

    result = {
        "allowed": not breaking,
        "breaking": breaking,
        "compatible": compatible,
        "version": version,
        "summary": summary,
        "audit": [],
    }
    # 门禁留痕：破坏性与兼容变更都记录
    result["audit"].append(_record({
        "action": "check", "version": version, "summary": summary,
        "allowed": result["allowed"],
        "breaking": len(breaking), "compatible": len(compatible), "ts": _now(),
    }))
    result["export"] = json.dumps(
        {k: v for k, v in result.items() if k not in ("export", "audit")},
        ensure_ascii=False, sort_keys=True)
    return result


def apply(plan):
    """按门禁结果应用变更：破坏性阻断，兼容变更按表比例灰度，失败自动回滚。"""
    plan = plan or {}
    tables = list(plan.get("tables", []))
    chk = plan.get("check") or {}
    breaking = list(chk.get("breaking", []))
    compatible = list(chk.get("compatible", []))
    summary = chk.get("summary", "无结构变更")
    audit = []

    # 审批留痕：审批人、原因、变更摘要
    audit.append(_record({
        "action": "approve", "approver": plan.get("approver"),
        "reason": plan.get("reason"), "summary": summary,
        "approved": bool(plan.get("approver") and plan.get("reason")), "ts": _now(),
    }))

    # 门禁：破坏性变更阻断，附迁移建议
    if breaking:
        audit.append(_record({
            "action": "block", "tables": tables, "summary": summary,
            "suggestions": [b["suggestion"] for b in breaking], "ts": _now(),
        }))
        return {"applied": [], "gray": [], "rolled_back": [], "blocked": True,
                "version": chk.get("version", _version), "audit": audit,
                "coexist": {"new": [], "old": tables}}

    # 无结构变更：直接应用，无需灰度
    if not compatible:
        version = _next_version()
        for t in tables:
            _bump(t, version)
        audit.append(_record({"action": "apply", "tables": tables,
                              "version": version, "ts": _now()}))
        return {"applied": tables, "gray": [], "rolled_back": [], "blocked": False,
                "version": version, "audit": audit,
                "coexist": {"new": tables, "old": []}}

    # 兼容变更：按表比例灰度，灰度期间新旧结构并存、读写兼容
    ratio = min(max(float(plan.get("gray_ratio", 0.5)), 0.0), 1.0)
    n_gray = min(max(1, int(round(len(tables) * ratio))), len(tables)) if tables else 0
    gray, rest = tables[:n_gray], tables[n_gray:]
    version = _next_version()
    for t in gray:
        _bump(t, version)
    audit.append(_record({"action": "gray", "tables": gray, "ratio": ratio,
                          "version": version, "ts": _now()}))

    # 灰度健康检查失败：自动回滚灰度表到上一版本，全量不生效
    failed = [t for t in gray if t in set(plan.get("fail_tables", []))]
    if failed:
        for t in gray:
            _rollback(t)
        audit.append(_record({
            "action": "rollback", "tables": gray, "failed": failed,
            "restored": {t: _versions.get(t, 0) for t in gray}, "ts": _now(),
        }))
        return {"applied": [], "gray": gray, "rolled_back": gray, "blocked": False,
                "version": version, "audit": audit,
                "coexist": {"new": [], "old": tables}}

    # 灰度成功：放量到全量
    for t in rest:
        _bump(t, version)
    audit.append(_record({"action": "apply", "tables": rest,
                          "version": version, "ts": _now()}))
    return {"applied": tables, "gray": gray, "rolled_back": [], "blocked": False,
            "version": version, "audit": audit,
            "coexist": {"new": gray, "old": rest}}


def audit_log():
    """返回全量审计留痕。"""
    return list(_audit)
