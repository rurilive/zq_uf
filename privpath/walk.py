"""提权路径梳理：从低权限账号出发，按操作逐跳记录可达权限、前置条件和阻断点。

模型：权限（role/effect）是节点，操作（op）是有向边 needs -> effect。
- walk(start) 枚举从起点角色出发的所有简单路径，按目标权限归档；
- 每一步记录所需前提、所用账号、产生效果和依赖的配置项；
- 走不通的分支（权限不可达 / 前置条件不满足）保留在 blocked 中，不丢弃；
- report() 汇总全部记录：配置依赖、配置变更影响、跨账号复现失败、测试账号清单。
"""

import copy

_PATHS = {}


def reset():
    """清空全部路径记录。"""
    _PATHS.clear()
    return {"paths": 0}


def _add_unique(seq, value):
    if value and value not in seq:
        seq.append(value)


def _step(op, account, status="ok", reason=None):
    """构造一步操作记录：前提、账号、效果、配置依赖齐全。"""
    item = {
        "op": op.get("op", ""),
        "needs": op.get("needs", ""),
        "effect": op.get("effect", ""),
        "account": op.get("account") or account,
        "preconditions": list(op.get("preconditions") or []),
        "config": list(op.get("config") or []),
        "status": status,
    }
    if reason:
        item["reason"] = reason
    return item


def walk(start):
    """从起点账号出发枚举提权路径。

    start 字段：
      account:      必填，起点账号
      role:         起始权限，默认 user
      ops:          操作列表，每项含 op/needs/effect，可选
                    preconditions（前提名列表）、config（依赖配置项列表）、
                    account（该步改用的账号，如窃得的服务账号凭据）
      conditions:   前置条件当前状态 {名称: bool}；显式为 False 才阻断，
                    未给出的前提只记录不判定，留给复现时人工确认

    返回：可达权限、按目标权限归档的路径、逐步步骤、前置条件、阻断点、涉及账号。
    """
    if not isinstance(start, dict) or not start.get("account"):
        raise ValueError("account is required")

    account = start["account"]
    role = start.get("role", "user")
    ops = start.get("ops") or []
    conditions = start.get("conditions") or {}

    by_need = {}
    for op in ops:
        by_need.setdefault(op.get("needs", ""), []).append(op)

    paths = []
    blocked = []
    blocked_keys = set()
    executed = {}
    reachable = []
    preconditions = []
    accounts = []

    def block(op, reason):
        key = (op.get("op", ""), reason)
        if key in blocked_keys:
            return
        blocked_keys.add(key)
        blocked.append(_step(op, account, status="blocked", reason=reason))

    def dfs(current, trail, visited):
        """沿 needs -> effect 逐跳深入，visited 保证成环时不死循环。"""
        for op in by_need.get(current, []):
            name = op.get("op", "")
            failed = [
                p for p in (op.get("preconditions") or [])
                if conditions.get(p) is False
            ]
            if failed:
                block(op, "precondition not satisfied: " + ", ".join(failed))
                continue

            step = _step(op, account)
            executed.setdefault(name, step)
            _add_unique(accounts, step["account"])
            for pre in step["preconditions"]:
                _add_unique(preconditions, pre)

            new_trail = trail + [step]
            effect = step["effect"]
            if not effect:
                # 可执行但不产生新权限的操作：步骤已记录，不再展开
                continue
            if effect in visited:
                # 回边指向已经获得的权限，不产生新权限，步骤留档即可
                continue
            _add_unique(reachable, effect)
            paths.append({"target": effect, "steps": list(new_trail)})
            dfs(effect, new_trail, visited | {effect})

    dfs(role, [], {role})

    # 遍历结束仍未执行的操作：走不通的分支同样留档，注明阻断原因
    for op in ops:
        name = op.get("op", "")
        if name in executed or any(b["op"] == name for b in blocked):
            continue
        block(op, "role '%s' not reachable" % op.get("needs", ""))

    by_target = {}
    for path in paths:
        by_target.setdefault(path["target"], []).append(path)

    previous = _PATHS.get(account)
    record = {
        "start": account,
        "role": role,
        "reachable": reachable,
        "paths": paths,
        "by_target": by_target,
        "steps": list(executed.values()),
        "preconditions": preconditions,
        "blocked": blocked,
        "accounts": accounts,
        "config": sorted({
            c for step in executed.values() for c in step["config"]
        }),
        "affected": [],
        # 重新 walk 会刷新分析结论，但历史跨账号复现记录保留
        "reproductions": previous.get("reproductions", []) if previous else [],
    }
    _PATHS[account] = record
    return copy.deepcopy(record)


def config_changed(items):
    """通知配置项发生变化，标出依赖这些配置的已记录路径。

    返回受影响清单；受影响标记累积保留，直到下一次 walk 重新分析该账号。
    """
    items = list(items)
    affected = []
    for record in _PATHS.values():
        hit = set()
        for path in record["paths"]:
            deps = {c for step in path["steps"] for c in step["config"]}
            hit.update(c for c in items if c in deps)
        if hit:
            record["affected"] = sorted(set(record["affected"]) | hit)
            affected.append({"start": record["start"], "changed": sorted(hit)})
    return {"changed": items, "affected": affected}


def record_reproduction(start, account, target, reason, ok=False):
    """记录一次跨账号复现结果。

    复现失败不删除原路径结论，只在原记录上追加失败原因和复现所用账号。
    """
    record = _PATHS.get(start)
    if record is None:
        raise ValueError("unknown start account: %s" % start)
    if target not in record["by_target"]:
        raise ValueError("target '%s' not reachable from %s" % (target, start))
    entry = {
        "account": account,
        "target": target,
        "ok": bool(ok),
        "reason": reason,
    }
    record["reproductions"].append(entry)
    return dict(entry)


def report():
    """汇总全部起点的路径，按目标权限归档，供按起点查看和逐步复现。"""
    records = list(_PATHS.values())
    by_target = {}
    blocked = []
    failures = []
    affected = []
    config_refs = {}
    accounts = set()
    preconditions = set()

    for record in records:
        start = record["start"]
        accounts.update(record["accounts"])
        preconditions.update(record["preconditions"])

        for path in record["paths"]:
            target = path["target"]
            by_target.setdefault(target, []).append(
                {"start": start, "steps": copy.deepcopy(path["steps"])}
            )
            for step in path["steps"]:
                for conf in step["config"]:
                    config_refs.setdefault(conf, set()).add(
                        "%s->%s" % (start, target)
                    )

        for branch in record["blocked"]:
            blocked.append({"start": start, **branch})

        for repro in record["reproductions"]:
            accounts.add(repro["account"])
            if not repro["ok"]:
                failures.append({"start": start, **repro})

        if record["affected"]:
            affected.append({"start": start, "changed": list(record["affected"])})

    return {
        "paths": sum(len(r["paths"]) for r in records),
        "starts": [r["start"] for r in records],
        "reachable": sorted(by_target),
        "by_target": by_target,
        "blocked": blocked,
        "blocked_count": len(blocked),
        "preconditions": sorted(preconditions),
        "config": {c: sorted(refs) for c, refs in sorted(config_refs.items())},
        "affected": affected,
        "failures": failures,
        "accounts": sorted(accounts),
    }
