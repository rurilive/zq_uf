"""容器逃逸检测入口。

check(container)：按配置组合判定，返回命中组合、风险等级、每项配置的实际
取值（依据）和按“不停机/需停机”分开的处置建议。
report()：同一主机的多个容器合并展示；同一配置（config_hash）在不同宿主
上的结论放在一起对比；误报标记单独成区并保留实际配置快照。
"""
from __future__ import annotations

from .model import Facts
from .rules import evaluate

_CONTAINERS = {}
_FALSE_POSITIVES = {}

_LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}
_FP_OPEN = {"none": 0, "low": 1, "medium": 2, "high": 3}


def reset():
    """清空容器记录与误报标记。"""
    count = len(_CONTAINERS)
    _CONTAINERS.clear()
    _FALSE_POSITIVES.clear()
    return {"containers": count}


def check(container):
    """对单个容器做组合判定并落库，返回该容器的完整结论。"""
    facts = Facts(container)
    level, combos, factor_hits, advice = evaluate(facts)

    record = {
        "container": facts.name,
        "host": facts.host,
        "image": facts.image,
        "config_hash": facts.config_hash(),
        "hit": bool(combos or factor_hits),
        "level": level,
        # 命中的配置组合：每条给出规则、等级和相关配置的实际取值。
        "combos": combos,
        # 完整判定依据：每项配置的实际取值，可逐项展开核对。
        "basis": facts.basis(),
        # 无高危命中时参与分级的残余风险因子。
        "factors": factor_hits,
        "advice": advice,
        "false_positive": False,
    }
    _CONTAINERS[(facts.host, facts.name)] = record
    return dict(record)


def mark_false_positive(host, container, reason="", actual_config=None):
    """安全同学对误报做标记，可带上实际配置作为复核依据。

    标记不改判定结论本身（配置可复算），只做标注并保留快照。
    """
    key = (host, container)
    if key not in _CONTAINERS:
        raise ValueError(f"container not found: {host}/{container}")
    record = _CONTAINERS[key]
    snapshot = actual_config if actual_config is not None else [
        dict(item) for item in record["basis"]
    ]
    mark = {
        "host": host,
        "container": container,
        "level": record["level"],
        "config_hash": record["config_hash"],
        "reason": reason,
        "actual_config": snapshot,
    }
    _FALSE_POSITIVES[key] = mark
    record["false_positive"] = True
    return dict(mark)


def _host_groups(records):
    """按主机合并，主机内容器可展开，整体按最高风险排序。"""
    by_host = {}
    for record in records:
        by_host.setdefault(record["host"], []).append(record)

    groups = []
    for host, items in by_host.items():
        items = sorted(items, key=lambda r: (_LEVEL_ORDER[r["level"]], r["container"]))
        worst = min((r["level"] for r in items), key=lambda lv: _LEVEL_ORDER[lv])
        groups.append({
            "host": host,
            "container_count": len(items),
            "highest_level": worst,
            "high": sum(1 for r in items if r["level"] == "high"),
            "medium": sum(1 for r in items if r["level"] == "medium"),
            "low": sum(1 for r in items if r["level"] == "low"),
            "none": sum(1 for r in items if r["level"] == "none"),
            "false_positives": sum(1 for r in items if r["false_positive"]),
            # 单容器结论整体保留，供逐容器展开。
            "containers": [dict(r) for r in items],
        })
    return sorted(groups, key=lambda g: (_LEVEL_ORDER[g["highest_level"]], g["host"]))


def _config_groups(records):
    """同一配置在不同宿主上的结论放在一起，验证跨宿主结论一致性。"""
    by_hash = {}
    for record in records:
        by_hash.setdefault(record["config_hash"], []).append(record)

    groups = []
    for config_hash, items in by_hash.items():
        levels = {r["level"] for r in items}
        groups.append({
            "config_hash": config_hash,
            "image": items[0]["image"],
            "host_count": len({r["host"] for r in items}),
            "container_count": len(items),
            "consistent": len(levels) == 1,
            "level": next(iter(levels)) if len(levels) == 1 else None,
            "levels_on_hosts": sorted(
                (
                    {"host": r["host"], "container": r["container"], "level": r["level"]}
                    for r in items
                ),
                key=lambda x: (x["host"], x["container"]),
            ),
            "basis": [dict(item) for item in items[0]["basis"]],
        })
    return sorted(groups, key=lambda g: (g["consistent"], -g["host_count"], g["config_hash"]))


def report():
    """汇总报告：主机合并视图 + 跨宿主同配置对比 + 误报标记。"""
    records = list(_CONTAINERS.values())
    active = [r for r in records if not r["false_positive"]]

    return {
        "containers": len(records),
        "hosts": len({r["host"] for r in records}),
        "high": sum(1 for r in active if r["level"] == "high"),
        "medium": sum(1 for r in active if r["level"] == "medium"),
        "low": sum(1 for r in active if r["level"] == "low"),
        "none": sum(1 for r in active if r["level"] == "none"),
        "false_positives": len(_FALSE_POSITIVES),
        # 同一主机的多个容器合并展示。
        "host_groups": _host_groups(records),
        # 同一配置在不同宿主上的结论对比。
        "config_groups": _config_groups(records),
        # 误报标记（带实际配置快照）。
        "marked": [dict(mark) for mark in _FALSE_POSITIVES.values()],
    }
