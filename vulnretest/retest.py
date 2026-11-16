"""漏洞复测记录。

复测沿用原验证步骤，并携带修复前基线：本轮每一步的结果与基线逐项比对，
全部通过才允许关闭工单；未通过时记录失败点和当前证据。没有基线（首次复测）
或验证步骤变化时，先以本轮结果重新生成基线，本轮不判定通过。关闭后再次
复现，按新问题处理并关联原工单。
"""

import copy
from datetime import datetime, timezone

_records = []
_state = {}


def _reset():
    """清空进程内状态，仅供测试使用。"""
    _records.clear()
    _state.clear()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalise_steps(raw, label):
    """校验并规范化步骤；空步骤或重名步骤直接报错，避免静默判通过。"""
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{label} 需要列表")
    if not raw:
        raise ValueError(f"{label}为空，不能据此判定复测结果")

    steps = []
    seen = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"{label} 第 {index} 项需要字典")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label} 第 {index} 项缺少步骤名")
        name = name.strip()
        if name in seen:
            raise ValueError(f"{label} 中步骤重名：{name}")
        seen.add(name)

        ok = item.get("ok")
        if not isinstance(ok, bool):
            raise ValueError(f"{label} 步骤 {name} 的 ok 需要布尔值")

        evidence = item.get("evidence")
        if evidence is not None and not isinstance(evidence, str):
            raise ValueError(f"{label} 步骤 {name} 的 evidence 需要链接字符串")

        reason = item.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ValueError(f"{label} 步骤 {name} 的 reason 需要字符串")

        steps.append({"name": name, "ok": ok, "evidence": evidence, "reason": reason})
    return steps


def _snapshot(steps):
    """基线和历史只保留对照所需的字段，不夹带当轮失败原因。"""
    return [
        {"name": step["name"], "ok": step["ok"], "evidence": step.get("evidence")}
        for step in steps
    ]


def _signature(steps):
    """验证步骤是否“沿用原步骤”只看步骤名和顺序。"""
    return tuple(step["name"] for step in steps)


def _extract_baseline(vuln):
    """修复前基线随漏洞传入：步骤列表，或含 steps/at 的字典。"""
    raw = vuln.get("baseline")
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw_steps = raw.get("steps")
        at = raw.get("at")
    elif isinstance(raw, (list, tuple)):
        raw_steps, at = raw, None
    else:
        raise ValueError("baseline 需要步骤列表或含 steps 的字典")

    if at is not None and not isinstance(at, str):
        raise ValueError("baseline.at 需要时间字符串")
    steps = _normalise_steps(raw_steps, "baseline.steps")
    return {"at": at, "source": "provided", "steps": _snapshot(steps)}


def _diff(baseline_steps, steps):
    """本轮结果与修复前基线逐项比对。"""
    before = {step["name"]: step for step in baseline_steps}
    rows = []
    for step in steps:
        prior = before[step["name"]]
        if step["ok"] == prior["ok"]:
            status = "unchanged"
        elif step["ok"]:
            status = "fixed"
        else:
            status = "regressed"
        rows.append(
            {
                "step": step["name"],
                "baseline_ok": prior["ok"],
                "ok": step["ok"],
                "status": status,
                "evidence_before": prior.get("evidence"),
                "evidence_now": step.get("evidence"),
            }
        )
    return rows


def _failures(steps, baseline_steps):
    """未通过的步骤逐项落失败点和当前证据。"""
    baseline_ok = {step["name"]: step["ok"] for step in baseline_steps}
    failures = []
    for step in steps:
        if step["ok"]:
            continue
        failures.append(
            {
                "step": step["name"],
                "reason": step.get("reason") or "验证步骤未通过",
                "evidence": step.get("evidence"),
                "baseline_ok": baseline_ok[step["name"]],
            }
        )
    return failures


def _since_last(previous, steps):
    """与上一次复测相比，哪些步骤结果变了。"""
    if previous is None:
        return None

    before = {step["name"]: step["ok"] for step in previous["steps"]}
    now = {step["name"]: step["ok"] for step in steps}
    changes = []
    for name, ok in now.items():
        if name not in before:
            changes.append({"step": name, "was_ok": None, "ok": ok})
        elif before[name] != ok:
            changes.append({"step": name, "was_ok": before[name], "ok": ok})
    for name, ok in before.items():
        if name not in now:
            changes.append({"step": name, "was_ok": ok, "ok": None})

    return {
        "round": previous["round"],
        "at": previous["at"],
        "result": previous["result"],
        "changed": bool(changes),
        "steps_changed": changes,
    }


def retest(vuln):
    """执行一轮复测。

    ``vuln`` 必填 ``id`` 和 ``steps``，可选 ``project``、``ticket``，以及
    带修复前证据的 ``baseline``（``{"steps": [...], "at": ...}``）。

    返回本轮结论（``pass``/``fail``/``baseline``）、与修复前基线的逐项差异、
    与上一次复测的差异、失败点、当前证据链接和工单关闭状态。只有 ``pass``
    会给工单置为关闭。
    """
    if not isinstance(vuln, dict):
        raise ValueError("vuln 需要字典")

    vuln_id = vuln.get("id")
    if not isinstance(vuln_id, str) or not vuln_id.strip():
        raise ValueError("缺少漏洞编号")
    vuln_id = vuln_id.strip()

    project = vuln.get("project")
    if project is not None and (not isinstance(project, str) or not project.strip()):
        raise ValueError("project 需要项目名")
    project = project.strip() if project is not None else None

    ticket_id = vuln.get("ticket")
    if ticket_id is not None and (not isinstance(ticket_id, str) or not ticket_id.strip()):
        raise ValueError("ticket 需要工单号")
    ticket_id = ticket_id.strip() if ticket_id is not None else None

    raw_steps = vuln.get("steps")
    if raw_steps is None:
        raise ValueError("缺少验证步骤")
    steps = _normalise_steps(raw_steps, "steps")
    signature = _signature(steps)

    state = _state.setdefault(
        vuln_id, {"baseline": None, "ticket": None, "round": 0, "recurrences": 0}
    )
    if ticket_id is not None and state["ticket"] is not None:
        current_id = state["ticket"]["id"]
        if current_id != ticket_id and state["ticket"]["original"] != ticket_id:
            raise ValueError(
                f"漏洞 {vuln_id} 已关联工单 {current_id}，与传入的 {ticket_id} 不一致"
            )

    provided = _extract_baseline(vuln)
    provided_usable = provided is not None and _signature(provided["steps"]) == signature
    stored = state["baseline"]
    step_changed = stored is not None and _signature(stored["steps"]) != signature

    regenerated = False
    established_now = False
    used_provided = False
    if step_changed:
        # 验证步骤变了，旧基线不再适用，以本轮结果重新生成基线，本轮不判通过。
        baseline = {"at": _now(), "source": "current_run", "steps": _snapshot(steps)}
        state["baseline"] = baseline
        regenerated = True
        established_now = True
    elif provided_usable:
        # 调用方给了与当前步骤一致的修复前证据，直接作为对照。
        baseline = provided
        state["baseline"] = baseline
        used_provided = True
    elif stored is not None:
        baseline = stored
    else:
        # 首次复测还没有基线，先以本轮结果建立基线，本轮不判通过。
        baseline = {"at": _now(), "source": "current_run", "steps": _snapshot(steps)}
        state["baseline"] = baseline
        established_now = True

    all_ok = all(step["ok"] for step in steps)
    if not all_ok:
        result = "fail"
    elif established_now:
        result = "baseline"
    else:
        result = "pass"

    previous = None
    for row in reversed(_records):
        if row["vuln"] == vuln_id:
            previous = row
            break

    at = _now()
    ticket = state["ticket"]
    if ticket is None:
        ticket = {"id": ticket_id or vuln_id, "status": "open", "original": None}
        state["ticket"] = ticket
    previous_status = ticket["status"]
    recurrence = None

    if result == "pass":
        # 比对通过才允许关闭，工单状态自动变化。
        ticket["status"] = "closed"
        ticket_info = {
            "id": ticket["id"],
            "status": "closed",
            "previous_status": previous_status,
            "changed": previous_status != "closed",
            "original": ticket["original"],
        }
    elif result == "fail" and ticket["status"] == "closed":
        # 关闭后再次复现：新开问题工单并关联原工单。
        original_ticket = ticket["id"]
        state["recurrences"] += 1
        new_ticket = f"{original_ticket}-R{state['recurrences']}"
        recurrence = {
            "new_ticket": new_ticket,
            "original_ticket": original_ticket,
            "reason": "关闭后再次出现",
            "handled_as": "new_issue",
            "baseline_reset": True,
        }
        ticket = {"id": new_ticket, "status": "open", "original": original_ticket}
        state["ticket"] = ticket
        ticket_info = {
            "id": new_ticket,
            "status": "open",
            "previous_status": None,
            "changed": True,
            "original": original_ticket,
        }
        # 新问题的修复前证据是本轮复现结果，供下次复测对照。
        state["baseline"] = {"at": at, "source": "recurrence", "steps": _snapshot(steps)}
    else:
        # 未通过或仅重建基线时，工单保持原状。
        ticket_info = {
            "id": ticket["id"],
            "status": ticket["status"],
            "previous_status": previous_status,
            "changed": False,
            "original": ticket["original"],
        }

    state["round"] += 1
    record = {
        "vuln": vuln_id,
        "project": project,
        "ticket": ticket_info,
        "round": state["round"],
        "at": at,
        "result": result,
        "closed": ticket_info["status"] == "closed",
        "step_count": len(steps),
        "baseline": {
            "at": baseline.get("at"),
            "source": baseline.get("source"),
            "regenerated": regenerated,
            "established": established_now,
            "ignored_provided": provided is not None and not used_provided,
            "steps": copy.deepcopy(baseline["steps"]),
        },
        # 本轮对照的就是这份基线；重建基线的这一轮不产生差异。
        "diff": [] if established_now else _diff(baseline["steps"], steps),
        "failures": _failures(steps, baseline["steps"]),
        "since_last": _since_last(previous, steps),
        "steps": copy.deepcopy(steps),
        "evidence": [
            {"step": step["name"], "url": step["evidence"]}
            for step in steps
            if step.get("evidence")
        ],
        "recurrence": recurrence,
    }
    _records.append(record)
    return copy.deepcopy(record)


def history(vuln=None, project=None):
    """按漏洞或项目查看复测记录，按时间排列并按项目汇总。"""
    if vuln is not None and (not isinstance(vuln, str) or not vuln.strip()):
        raise ValueError("vuln 需要漏洞编号")
    if project is not None and (not isinstance(project, str) or not project.strip()):
        raise ValueError("project 需要项目名")
    vuln = vuln.strip() if vuln is not None else None
    project = project.strip() if project is not None else None

    scoped = [row for row in _records if vuln is None or row["vuln"] == vuln]
    rows = [row for row in scoped if project is None or row["project"] == project]
    rows.sort(key=lambda row: row["at"])

    by_project = {}
    for row in rows:
        key = row["project"] or "(未指定)"
        bucket = by_project.setdefault(
            key, {"count": 0, "pass": 0, "fail": 0, "baseline": 0, "closed": 0}
        )
        bucket["count"] += 1
        bucket[row["result"]] += 1
        if row["closed"]:
            bucket["closed"] += 1

    return {
        "vuln": vuln,
        "project": project,
        "count": len(rows),
        "records": copy.deepcopy(rows),
        "projects": sorted({row["project"] for row in _records if row["project"]}),
        "by_project": by_project,
    }
