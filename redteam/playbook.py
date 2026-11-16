"""红队演练的阶段编排、留证时间线与权限回收。

- ``plan(exercise)`` 登记演练：阶段目标、执行人、依赖（``after``）和演练负责人。
- ``start/complete/fail/rollback`` 推进阶段：前置阶段没完成时 ``start`` 返回
  ``blocked`` 并列出 ``blocked_by``；完成或失败一个阶段都不会自动开始后续阶段。
- ``acquire`` 在阶段执行中登记已经拿到的权限。
- ``halt`` 由演练负责人发起，叫停后把已获权限逐项列入回收清单。
- ``recover`` 逐项确认回收结果；没回收成功的单独列出并保留卡点说明。
- 演练工作结束后，只有权限全部回收演练才算 ``closed``。
- ``timeline()`` 按发生顺序给出复盘素材；``plan()`` 与 ``timeline()`` 都返回
  阶段状态、负责人、已获权限和回收结果。
"""

from datetime import datetime, timezone

_STAGE_TERMINAL = ("done", "failed", "stopped")

_exercises = {}


class PlaybookError(ValueError):
    """演练编排中的参数或状态错误。"""


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _clean_name(value, what):
    if not isinstance(value, str) or not value.strip():
        raise PlaybookError(f"{what}需要非空字符串")
    return value.strip()


def _stamp(at=None):
    """归一化时间：datetime 转 ISO 8601（naive 按 UTC），字符串原样保留。"""
    if at is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(at, datetime):
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at.isoformat()
    if isinstance(at, str) and at.strip():
        return at.strip()
    raise PlaybookError(f"时间需要 datetime 或非空字符串，收到 {type(at).__name__}")


def _require(exercise):
    name = _clean_name(exercise, "演练名称")
    record = _exercises.get(name)
    if record is None:
        raise PlaybookError(f"未知演练: {name}")
    return record


def _require_stage(record, stage):
    name = _clean_name(stage, "阶段名称")
    for item in record["stages"]:
        if item["name"] == name:
            return item
    raise PlaybookError(f"未知阶段: {name}")


def _stage_by_name(record, name):
    for item in record["stages"]:
        if item["name"] == name:
            return item
    return None


def _event(record, kind, stage=None, owner=None, detail=None, at=None, status=None):
    entry = {
        "seq": len(record["events"]) + 1,
        "at": _stamp(at),
        "type": kind,
        "stage": stage,
        "owner": owner,
        "status": status,
        "detail": detail,
    }
    record["events"].append(entry)
    return entry


def _all_recovered(record):
    return all(item["status"] == "recovered" for item in record["assets"])


def _status(record):
    """演练整体状态：planned/running/failed/finished/halted/closed。"""
    stages = record["stages"]
    if record["halted"]:
        return "closed" if _all_recovered(record) else "halted"
    if record["closed_at"]:
        return "closed"
    if stages and all(s["status"] == "done" for s in stages):
        return "closed" if _all_recovered(record) else "finished"
    if any(s["status"] == "failed" for s in stages):
        return "failed"
    if any(s["status"] != "pending" for s in stages):
        return "running"
    return "planned"


def _maybe_close(record, at):
    """全部阶段完成后落 finished；工作结束且权限全部回收后落 closed。"""
    if _status(record) == "closed":
        if record["closed_at"]:
            return
        record["closed_at"] = _stamp(at)
        _event(record, "exercise_closed", owner=record["owner"],
               detail="权限回收完成，演练结束", at=at, status="closed")
        return
    if record.get("finished_at"):
        return
    stages = record["stages"]
    if not record["halted"] and stages and all(s["status"] == "done" for s in stages):
        record["finished_at"] = _stamp(at)
        _event(record, "exercise_finished", owner=record["owner"],
               detail="阶段全部完成，等待权限回收", at=at, status="finished")


def _ensure_open(record):
    if record["halted"]:
        raise PlaybookError("演练已叫停，不能再推进阶段")
    if record["closed_at"]:
        raise PlaybookError("演练已结束")


def _held_snapshot(record):
    return [
        {"name": item["name"], "stage": item["stage"], "status": item["status"]}
        for item in record["assets"]
        if item["status"] != "recovered"
    ]


def _progress(record):
    """按执行人汇总阶段进度，供负责人查看。"""
    order = []
    buckets = {}
    for stage in record["stages"]:
        key = stage["owner"] if stage["owner"] is not None else "未分派"
        if key not in buckets:
            buckets[key] = {
                "owner": stage["owner"],
                "total": 0,
                "done": 0,
                "running": 0,
                "failed": 0,
                "pending": 0,
                "stopped": 0,
                "stages": [],
            }
            order.append(key)
        bucket = buckets[key]
        bucket["total"] += 1
        bucket[stage["status"]] = bucket.get(stage["status"], 0) + 1
        bucket["stages"].append({"name": stage["name"], "status": stage["status"]})
    return {
        "total": len(record["stages"]),
        "done": sum(1 for s in record["stages"] if s["status"] == "done"),
        "by_owner": {key: buckets[key] for key in order},
    }


def _copy_stage(stage):
    return {
        "name": stage["name"],
        "goal": stage["goal"],
        "owner": stage["owner"],
        "after": list(stage["after"]),
        "status": stage["status"],
        "started": stage["started"],
        "finished": stage["finished"],
        "result": stage["result"],
        "failure": stage["failure"],
        "failure_assets": [dict(item) for item in stage["failure_assets"]],
        "assets": list(stage["assets"]),
    }


def _copy_asset(asset):
    return {key: value for key, value in asset.items() if not key.startswith("_")}


def _snapshot(record):
    assets = [_copy_asset(item) for item in record["assets"]]
    return {
        "exercise": record["name"],
        "owner": record["owner"],
        "status": _status(record),
        "halted": record["halted"],
        "halt": dict(record["halt"]) if record["halt"] else None,
        "stages": [_copy_stage(item) for item in record["stages"]],
        "assets": assets,
        "recovered": [item for item in assets if item["status"] == "recovered"],
        "unrecovered": [item for item in assets if item["status"] == "unrecovered"],
        "pending_recovery": [item for item in assets if item["status"] == "held"],
        "progress": _progress(record),
    }


def _validate_dependencies(stages):
    names = [item["name"] for item in stages]
    known = set(names)
    graph = {}
    for item in stages:
        deps = []
        for dep in item["after"]:
            if dep not in known:
                raise PlaybookError(f"阶段 {item['name']} 依赖未知阶段: {dep}")
            if dep not in deps:
                deps.append(dep)
        item["after"] = deps
        graph[item["name"]] = deps

    visiting = set()
    visited = set()

    def visit(name, path):
        if name in visited:
            return
        if name in visiting:
            raise PlaybookError("阶段依赖存在环: " + " -> ".join(path + [name]))
        visiting.add(name)
        for dep in graph[name]:
            visit(dep, path + [name])
        visiting.discard(name)
        visited.add(name)

    for name in names:
        visit(name, [])


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def plan(exercise):
    """登记演练计划，返回阶段状态、负责人、权限与回收结果。"""
    if not isinstance(exercise, dict):
        raise PlaybookError("exercise 需要字典")
    name = _clean_name(exercise.get("name"), "演练名称")
    owner = exercise.get("owner")
    if owner is not None:
        owner = _clean_name(owner, "演练负责人")
    raw_stages = exercise.get("stages", [])
    if not isinstance(raw_stages, (list, tuple)):
        raise PlaybookError("stages 需要列表")

    stages = []
    seen = set()
    for item in raw_stages:
        if not isinstance(item, dict):
            raise PlaybookError("阶段需要字典")
        stage_name = _clean_name(item.get("name"), "阶段名称")
        if stage_name in seen:
            raise PlaybookError(f"阶段名称重复: {stage_name}")
        seen.add(stage_name)
        after = item.get("after", [])
        if not isinstance(after, (list, tuple)):
            raise PlaybookError(f"阶段 {stage_name} 的 after 需要列表")
        stage_owner = item.get("owner")
        if stage_owner is not None:
            stage_owner = _clean_name(stage_owner, f"阶段 {stage_name} 的负责人")
        stages.append(
            {
                "name": stage_name,
                "goal": item.get("goal"),
                "owner": stage_owner,
                "after": [_clean_name(dep, "前置阶段名称") for dep in after],
                "status": "pending",
                "started": None,
                "finished": None,
                "result": None,
                "failure": None,
                "failure_assets": [],
                "assets": [],
                "attempt": 0,
            }
        )
    _validate_dependencies(stages)

    record = {
        "name": name,
        "owner": owner,
        "halted": False,
        "halt": None,
        "finished_at": None,
        "closed_at": None,
        "stages": stages,
        "assets": [],
        "events": [],
    }
    _exercises[name] = record
    return _snapshot(record)


def start(exercise, stage, owner=None, at=None):
    """开始阶段；前置没完成时返回 blocked 与未完成的前置阶段。"""
    record = _require(exercise)
    item = _require_stage(record, stage)
    _ensure_open(record)
    if owner is not None:
        item["owner"] = _clean_name(owner, "执行人")
    if item["status"] == "running":
        raise PlaybookError(f"阶段已在执行中: {item['name']}")
    if item["status"] in _STAGE_TERMINAL:
        raise PlaybookError(f"阶段已结束: {item['name']}，如需重做请先回退")

    blocked = []
    for dep in item["after"]:
        dep_stage = _stage_by_name(record, dep)
        if dep_stage is None or dep_stage["status"] != "done":
            blocked.append(dep)
    if blocked:
        return {
            "exercise": record["name"],
            "stage": item["name"],
            "status": "blocked",
            "owner": item["owner"],
            "started": None,
            "blocked_by": blocked,
        }

    item["status"] = "running"
    item["attempt"] += 1
    item["started"] = _stamp(at)
    item["finished"] = None
    _event(record, "stage_started", stage=item["name"], owner=item["owner"],
           detail=item["goal"], at=at, status="running")
    return {
        "exercise": record["name"],
        "stage": item["name"],
        "status": "running",
        "owner": item["owner"],
        "started": item["started"],
        "blocked_by": [],
    }


def complete(exercise, stage, result=None, at=None):
    """结束阶段并登记结果；不会自动开始后续阶段。"""
    record = _require(exercise)
    item = _require_stage(record, stage)
    _ensure_open(record)
    if item["status"] != "running":
        raise PlaybookError(f"阶段未在执行中: {item['name']}，当前状态 {item['status']}")
    item["status"] = "done"
    item["finished"] = _stamp(at)
    item["result"] = result
    _event(record, "stage_completed", stage=item["name"], owner=item["owner"],
           detail=result, at=at, status="done")
    _maybe_close(record, at)
    return {
        "exercise": record["name"],
        "stage": item["name"],
        "status": "done",
        "owner": item["owner"],
        "started": item["started"],
        "finished": item["finished"],
        "result": item["result"],
        "next": None,
    }


def fail(exercise, stage, reason, at=None):
    """记录阶段失败原因和失败当时的权限状态；后续阶段保持待执行。"""
    record = _require(exercise)
    item = _require_stage(record, stage)
    _ensure_open(record)
    if item["status"] != "running":
        raise PlaybookError(f"阶段未在执行中: {item['name']}，当前状态 {item['status']}")
    reason = _clean_name(reason, "失败原因")
    item["status"] = "failed"
    item["finished"] = _stamp(at)
    item["failure"] = reason
    item["failure_assets"] = _held_snapshot(record)
    _event(record, "stage_failed", stage=item["name"], owner=item["owner"],
           detail={"reason": reason, "assets": item["failure_assets"]},
           at=at, status="failed")
    return {
        "exercise": record["name"],
        "stage": item["name"],
        "status": "failed",
        "owner": item["owner"],
        "started": item["started"],
        "finished": item["finished"],
        "reason": reason,
        "assets": [dict(a) for a in item["failure_assets"]],
        "next": None,
    }


def acquire(exercise, stage, asset, detail=None, at=None):
    """登记阶段执行中拿到的权限。"""
    record = _require(exercise)
    item = _require_stage(record, stage)
    _ensure_open(record)
    if item["status"] != "running":
        raise PlaybookError(f"阶段未在执行中: {item['name']}，不能登记权限")
    if isinstance(asset, dict):
        name = _clean_name(asset.get("name"), "权限名称")
        detail = asset.get("detail", detail)
    else:
        name = _clean_name(asset, "权限名称")
    for existing in record["assets"]:
        if existing["name"] != name:
            continue
        # 回退后重跑同一阶段会再次登记手上已有的权限：同一次执行内重复登记仍报错，
        # 跨执行（阶段重新开始过）则按幂等处理，不重复入账。
        if existing["stage"] == item["name"] and existing["_attempt"] != item["attempt"]:
            if detail is not None:
                existing["detail"] = detail
            return {
                "exercise": record["name"],
                "stage": item["name"],
                "asset": name,
                "status": existing["status"],
                "repeated": True,
                "assets": [_copy_asset(a) for a in record["assets"]],
            }
        raise PlaybookError(f"权限已登记: {name}（阶段 {existing['stage']}）")
    entry = {
        "name": name,
        "stage": item["name"],
        "detail": detail,
        "status": "held",
        "acquired_at": _stamp(at),
        "recovered_at": None,
        "recovered_by": None,
        "note": None,
        "_attempt": item["attempt"],
    }
    record["assets"].append(entry)
    item["assets"].append(name)
    _event(record, "asset_acquired", stage=item["name"], owner=item["owner"],
           detail=name, at=at, status="held")
    return {
        "exercise": record["name"],
        "stage": item["name"],
        "asset": name,
        "status": "held",
        "repeated": False,
        "assets": [_copy_asset(a) for a in record["assets"]],
    }


def rollback(exercise, stage, reason, at=None):
    """回退阶段到待执行并记录原因。"""
    record = _require(exercise)
    item = _require_stage(record, stage)
    _ensure_open(record)
    reason = _clean_name(reason, "回退原因")
    if item["status"] == "pending":
        raise PlaybookError(f"阶段已是待执行状态: {item['name']}")

    progressed = []
    for other in record["stages"]:
        if item["name"] in other["after"] and other["status"] != "pending":
            progressed.append({"name": other["name"], "status": other["status"]})
    if progressed:
        names = "、".join(f"{p['name']}({p['status']})" for p in progressed)
        raise PlaybookError(f"后续阶段已推进，不能回退 {item['name']}: {names}，请先回退后续阶段")

    item["status"] = "pending"
    item["started"] = None
    item["finished"] = None
    item["result"] = None
    item["failure"] = None
    item["failure_assets"] = []
    _event(record, "stage_rolled_back", stage=item["name"], owner=item["owner"],
           detail=reason, at=at, status="pending")
    return {
        "exercise": record["name"],
        "stage": item["name"],
        "status": "pending",
        "owner": item["owner"],
        "reason": reason,
    }


def halt(exercise, reason, by=None, at=None):
    """负责人叫停演练：停掉执行中的阶段并登记已获权限，进入逐项回收。"""
    record = _require(exercise)
    reason = _clean_name(reason, "叫停原因")
    if record["halted"]:
        raise PlaybookError("演练已叫停")
    if _status(record) == "closed":
        raise PlaybookError("演练已结束")
    if record["owner"] is not None:
        if by is None:
            by = record["owner"]  # 未指明时按演练负责人发起
        elif _clean_name(by, "叫停人") != record["owner"]:
            raise PlaybookError(f"只有负责人 {record['owner']} 可以叫停演练")
    elif by is not None:
        by = _clean_name(by, "叫停人")

    record["halted"] = True
    record["halt"] = {
        "reason": reason,
        "by": by,
        "at": _stamp(at),
        "assets": _held_snapshot(record),
    }
    stopped = []
    for item in record["stages"]:
        if item["status"] == "running":
            item["status"] = "stopped"
            item["finished"] = _stamp(at)
            stopped.append(item["name"])
            _event(record, "stage_stopped", stage=item["name"], owner=item["owner"],
                   detail=reason, at=at, status="stopped")
    _event(record, "halted", owner=by if by is not None else record["owner"],
           detail={"reason": reason, "stopped": stopped,
                   "assets": [a["name"] for a in record["assets"]]},
           at=at, status="halted")
    _maybe_close(record, at)
    snapshot = _snapshot(record)
    snapshot.update({"reason": reason, "by": by, "stopped": stopped})
    return snapshot


def recover(exercise, asset, ok=True, note=None, by=None, at=None):
    """逐项确认权限回收；回收失败必须写明卡在哪里。"""
    record = _require(exercise)
    name = _clean_name(asset, "权限名称")
    entry = None
    for item in record["assets"]:
        if item["name"] == name:
            entry = item
            break
    if entry is None:
        raise PlaybookError(f"未知权限: {name}")
    if entry["status"] == "recovered":
        raise PlaybookError(f"权限已回收: {name}")
    if not record["halted"]:
        # 失败或回退后可能重试，权限仍在使用；只有叫停或全部阶段完成后才回收。
        if any(s["status"] == "running" for s in record["stages"]):
            raise PlaybookError("演练仍在执行，请先叫停后再回收权限")
        if _status(record) not in ("finished", "closed"):
            raise PlaybookError("演练尚未结束，请先叫停或完成全部阶段再回收权限")
    if not ok:
        note = _clean_name(note, "未回收原因")
    else:
        note = note.strip() if isinstance(note, str) and note.strip() else None

    if ok:
        entry["status"] = "recovered"
        entry["recovered_at"] = _stamp(at)
        entry["recovered_by"] = by
        entry["note"] = note
        kind = "asset_recovered"
    else:
        entry["status"] = "unrecovered"
        entry["recovered_at"] = None
        entry["recovered_by"] = by
        entry["note"] = note
        kind = "asset_unrecovered"
    _event(record, kind, stage=entry["stage"], owner=by,
           detail={"asset": name, "note": note}, at=at, status=entry["status"])
    _maybe_close(record, at)

    snapshot = _snapshot(record)
    snapshot.update(
        {
            "asset": name,
            "asset_status": entry["status"],
            "ok": bool(ok),
            "note": note,
        }
    )
    return snapshot


def timeline(exercise):
    """复盘时间线：事件按发生顺序排列，附阶段状态、负责人与回收结果。"""
    record = _require(exercise)
    return {
        "exercise": record["name"],
        "owner": record["owner"],
        "status": _status(record),
        "halted": record["halted"],
        "stages": [_copy_stage(item) for item in record["stages"]],
        "entries": [dict(item) for item in record["events"]],
        "assets": [_copy_asset(item) for item in record["assets"]],
        "recovered": [dict(item) for item in record["assets"] if item["status"] == "recovered"],
        "unrecovered": [dict(item) for item in record["assets"] if item["status"] == "unrecovered"],
        "pending_recovery": [dict(item) for item in record["assets"] if item["status"] == "held"],
        "progress": _progress(record),
    }


def progress(exercise):
    """负责人按演练查看各执行人的阶段进度。"""
    record = _require(exercise)
    result = _progress(record)
    result.update(
        {
            "exercise": record["name"],
            "owner": record["owner"],
            "status": _status(record),
            "halted": record["halted"],
            "assets": [_copy_asset(item) for item in record["assets"]],
            "recovered": [dict(item) for item in record["assets"] if item["status"] == "recovered"],
            "unrecovered": [dict(item) for item in record["assets"] if item["status"] == "unrecovered"],
        }
    )
    return result
