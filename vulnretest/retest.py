"""漏洞复测：沿用原验证步骤，与修复前的基线逐项比对。

每轮复测都会记录：

- 本轮结果（``pass``/``fail``）以及每一步的当前证据和失败点；
- 与上一轮相比的差异（``fixed``/``regressed``/``unchanged``/``new``/``removed``）；
- 本轮采用的基线（修复前的验证步骤和证据）。步骤名称、顺序或预期变化时重新
  生成基线，旧记录保留各自当时的基线，不会跟着变；
- 工单关闭状态。通过后工单自动关闭，未通过的保持原状；
  关闭之后再次复现的问题按新问题处理，新工单关联原工单。

复测记录按时间排列，可按漏洞或项目查看、筛选与汇总。
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

PASS = "pass"
FAIL = "fail"
OPEN = "open"
CLOSED = "closed"

TICKET_STATUSES = (OPEN, CLOSED)

# diff 中每一步的变化类型
FIXED = "fixed"
REGRESSED = "regressed"
UNCHANGED = "unchanged"
NEW = "new"
REMOVED = "removed"

_records = []   # 全部复测记录，按写入顺序保存
_state = {}     # 漏洞编号 -> 工单、基线、上一轮步骤
_seq = 0
_last_at = None


class RetestError(ValueError):
    """复测入参或历史状态不合法。"""


def _reset_state():
    """清空进程内记录，供测试与本地演示使用。"""
    global _seq, _last_at
    _records.clear()
    _state.clear()
    _seq = 0
    _last_at = None


# 对外公开的别名，调用方不必依赖私有名字
reset = _reset_state


def _stamp(now=None):
    """返回 UTC 时间戳。

    未指定 ``now`` 时保证时间严格递增，这样连续复测按时间排列不会打平；
    指定 ``now`` 时尊重调用方给出的时间，排序时有 ``seq`` 兜底。
    """
    global _last_at
    if now is None:
        at = datetime.now(timezone.utc)
        if _last_at is not None and at <= _last_at:
            at = _last_at + timedelta(microseconds=1)
    elif isinstance(now, datetime):
        at = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        at = at.astimezone(timezone.utc)
    else:
        raise RetestError("now 需要 datetime")
    if _last_at is None or at > _last_at:
        _last_at = at
    return at.isoformat(timespec="microseconds")


def _at_value(value, default):
    """把调用方给出的时间整理成 ISO 字符串；缺省时用 default。"""
    if value is None:
        return default
    if isinstance(value, datetime):
        at = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, str) and value:
        return value
    raise RetestError("baseline.at 需要 datetime 或 ISO 字符串")


def _evidence(value):
    """把证据整理成链接列表，单条字符串也接受。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _dedup(items):
    out = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _checked_vuln(vuln):
    if not isinstance(vuln, dict):
        raise RetestError("vuln 需要字典")
    vuln_id = vuln.get("id")
    if not isinstance(vuln_id, str) or not vuln_id:
        raise RetestError("缺少漏洞编号")
    project = vuln.get("project")
    if project is not None and not isinstance(project, str):
        raise RetestError("project 需要字符串")
    ticket = vuln.get("ticket") or vuln_id
    if not isinstance(ticket, str) or not ticket:
        raise RetestError("ticket 需要字符串")
    status = vuln.get("status", vuln.get("ticket_status", OPEN))
    if status not in TICKET_STATUSES:
        raise RetestError(f"ticket_status 只能是 {OPEN}/{CLOSED}")
    return vuln_id, project, ticket, status


def _parse_steps(vuln_id, raw, baseline):
    """解析本轮步骤。

    未写 ``name`` 时按位置沿用基线步骤名；未写 ``expect`` 时按名称沿用基线的
    预期，这样复测只需要给出本轮的实际结果和证据。
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise RetestError(f"{vuln_id} 的 steps 需要列表")
    base_names = baseline["step_names"] if baseline else []
    base_expect = {
        step["name"]: step.get("expect", True) for step in baseline["steps"]
    } if baseline else {}
    steps, seen = [], set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RetestError(f"{vuln_id} 的 step 需要字典")
        name = item.get("name")
        if name in (None, ""):
            # 没写名字时沿用基线的同名位置，首轮则按位置生成稳定名称
            name = base_names[index] if index < len(base_names) else f"步骤{index + 1}"
        if not isinstance(name, str) or not name:
            raise RetestError(f"{vuln_id} 的 step 缺少 name")
        if name in seen:
            raise RetestError(f"{vuln_id} 的步骤名称重复: {name}")
        seen.add(name)
        if "expect" in item:
            expect = bool(item["expect"])
        elif "expected" in item:
            expect = bool(item["expected"])
        else:
            expect = bool(base_expect.get(name, True))
        steps.append({
            "name": name,
            "ok": bool(item.get("ok")),
            "expect": expect,
            "evidence": _evidence(item.get("evidence")),
            "reason": item.get("reason") or item.get("failure"),
        })
    return steps


def _signature(steps):
    """步骤名称与预期共同构成验证步骤的指纹。"""
    return [(step["name"], step["expect"]) for step in steps]


def _baseline_signature(baseline):
    return [(step["name"], step.get("expect", True)) for step in baseline["steps"]]


def _step_names(steps):
    return [step["name"] for step in steps]


def _carried_map(baseline):
    if not baseline:
        return {}
    return {step["name"]: step for step in baseline["steps"]}


def _build_baseline(vuln_id, issue, steps, at, reason, seq,
                    carried=None, extra_evidence=()):
    """按本轮步骤生成基线。

    同名步骤沿用 carried（上一版基线）里的修复前证据；此前没有记录的步骤，
    以生成基线时的本轮证据作为基线证据。
    """
    carried = carried or {}
    baseline_steps, evidence = [], list(extra_evidence)
    for step in steps:
        prior = carried.get(step["name"])
        before_evidence = (
            list(prior["evidence"]) if prior is not None else list(step["evidence"])
        )
        baseline_steps.append({
            "name": step["name"],
            "expect": step["expect"],
            "evidence": before_evidence,
            "reason": prior.get("reason") if prior else step["reason"],
        })
        evidence.extend(before_evidence)
    return {
        "id": f"{vuln_id}-B{seq}",
        "vuln": vuln_id,
        "issue": issue,
        "at": at,
        "reason": reason,
        "step_names": [step["name"] for step in baseline_steps],
        "steps": baseline_steps,
        "evidence": _dedup(evidence),
    }


def _provided_baseline(vuln_id, issue, raw, at, seq, extra_evidence=()):
    """解析调用方传入的修复前基线；格式不对时返回 None，由本轮步骤兜底。"""
    if not isinstance(raw, dict):
        return None
    items = raw.get("steps")
    if not isinstance(items, (list, tuple)) or not items:
        return None
    steps = []
    for item in items:
        if isinstance(item, str):
            steps.append({"name": item, "expect": True, "evidence": [], "reason": None})
            continue
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        if not isinstance(name, str) or not name:
            return None
        expect = item.get("expect", item.get("expected", True))
        steps.append({
            "name": name,
            "expect": bool(expect),
            "evidence": _evidence(item.get("evidence")),
            "reason": item.get("reason"),
        })
    names = [step["name"] for step in steps]
    if len(set(names)) != len(names):
        return None
    evidence = list(extra_evidence) + _evidence(raw.get("evidence"))
    for step in steps:
        evidence.extend(step["evidence"])
    return {
        "id": f"{vuln_id}-B{seq}",
        "vuln": vuln_id,
        "issue": issue,
        "at": _at_value(raw.get("at"), at),
        "reason": "provided",
        "step_names": names,
        "steps": steps,
        "evidence": _dedup(evidence),
    }


def _step_changes(before, after):
    if before is None:
        return {"added": [], "removed": []}
    before_names, after_names = before["step_names"], after
    return {
        "added": [name for name in after_names if name not in before_names],
        "removed": [name for name in before_names if name not in after_names],
    }


def _compare(baseline, steps):
    """把本轮结果与修复前基线逐项对照，返回对照表和失败点。"""
    current = {step["name"]: step for step in steps}
    before_by_name = {step["name"]: step for step in baseline["steps"]}
    comparison, failures = [], []
    for name in baseline["step_names"]:
        step = current.get(name)
        before = before_by_name.get(name, {})
        if step is None:
            failures.append({
                "step": name,
                "reason": "本轮未执行原验证步骤，无法确认修复",
                "expected": before.get("expect", True),
                "current": None,
                "evidence": [],
                "evidence_before": list(before.get("evidence", [])),
            })
            continue
        expect = bool(before.get("expect", True))
        match = step["ok"] is expect
        comparison.append({
            "step": name,
            "expected": expect,
            "current": step["ok"],
            "match": match,
            "evidence_before": list(before.get("evidence", [])),
            "evidence": list(step["evidence"]),
        })
        if not match:
            failures.append({
                "step": name,
                "reason": step["reason"] or "步骤结果与修复前基线不一致",
                "expected": expect,
                "current": step["ok"],
                "evidence": list(step["evidence"]),
                "evidence_before": list(before.get("evidence", [])),
            })
    return comparison, failures


def _diff(previous_steps, steps):
    """与上一轮相比的差异。

    ``diff`` 只列出发生变化的步骤（fixed/regressed/new/removed），和上次一样
    时为空列表；``diff_summary`` 给出全部步骤按变化类型的计数。首轮没有上一轮，
    ``diff`` 为空、``changed`` 为 False。
    """
    summary = {FIXED: 0, REGRESSED: 0, UNCHANGED: 0, NEW: 0, REMOVED: 0}
    summary["total"] = 0
    if previous_steps is None:
        return [], summary, False
    before = {step["name"]: bool(step["ok"]) for step in previous_steps}
    after_names = {step["name"] for step in steps}
    diff = []
    for step in steps:
        name = step["name"]
        if name not in before:
            change = NEW
        elif step["ok"] == before[name]:
            change = UNCHANGED
        elif step["ok"]:
            change = FIXED
        else:
            change = REGRESSED
        summary[change] += 1
        summary["total"] += 1
        if change == UNCHANGED:
            continue
        diff.append({
            "step": name,
            "previous": before.get(name),
            "current": step["ok"],
            "change": change,
        })
    for name, ok in before.items():
        if name in after_names:
            continue
        summary[REMOVED] += 1
        summary["total"] += 1
        diff.append({
            "step": name,
            "previous": ok,
            "current": None,
            "change": REMOVED,
        })
    return diff, summary, bool(diff)


def _issue_ticket(origin, issue):
    """首次为原工单，之后每次复现使用 <原工单>-R<n> 关联新问题。"""
    return origin if issue <= 1 else f"{origin}-R{issue}"


def _resolve_baseline(state, steps, provided_raw, at, fix_evidence):
    """决定本轮采用的基线，返回 (baseline, reset)。

    ``reset`` 表示本轮重新生成了基线。

    规则：
    - 首轮采用调用方给的基线（步骤对得上）；否则用本轮步骤生成初始基线；
    - 步骤名称/顺序/预期变化时重新生成基线，同名步骤沿用更早的修复前证据；
    - 其余情况继续沿用原基线。
    """
    current = state["baseline"]
    if current is None:
        provided = _provided_baseline(
            vuln_id=state["vuln"], issue=state["issue"], raw=provided_raw,
            at=at, seq=state["baseline_seq"] + 1, extra_evidence=fix_evidence,
        )
        state["baseline_seq"] += 1
        if provided is not None and (
            not steps or _baseline_signature(provided) == _signature(steps)
        ):
            return provided, False
        return _build_baseline(
            state["vuln"], state["issue"], steps, at,
            "steps_changed" if provided else "initial",
            state["baseline_seq"], _carried_map(provided), fix_evidence,
        ), provided is not None
    if steps and _baseline_signature(current) != _signature(steps):
        state["baseline_seq"] += 1
        return _build_baseline(
            state["vuln"], state["issue"], steps, at, "steps_changed",
            state["baseline_seq"], _carried_map(current), fix_evidence,
        ), True
    return current, False


def _new_state(vuln_id, project, origin_ticket, status):
    return {
        "vuln": vuln_id,
        "project": project,
        "origin_ticket": origin_ticket,
        "ticket": origin_ticket,
        "issue": 1,
        "status": status,
        "baseline": None,
        "baseline_seq": 0,
        "round": 0,
        "issue_round": 0,
        "last_steps": None,
        "previous_result": None,
    }


def retest(vuln, now=None):
    """跑一轮复测并记录，返回结果、与上次的差异、失败点和关闭状态。"""
    vuln_id, project, origin_ticket, initial_status = _checked_vuln(vuln)
    global _seq

    state = _state.get(vuln_id)
    if state is None:
        state = _new_state(vuln_id, project, origin_ticket, initial_status)
        _state[vuln_id] = state
    if project is not None:
        state["project"] = project

    at = _stamp(now)
    steps = _parse_steps(vuln_id, vuln.get("steps"), state["baseline"])
    fix_evidence = _evidence(vuln.get("evidence"))

    # 本轮没有给步骤时沿用原验证步骤，但不执行就无法确认修复，按失败记录。
    if not steps and state["baseline"] is not None:
        steps = [
            {
                "name": step["name"],
                "ok": False,
                "expect": step.get("expect", True),
                "evidence": [],
                "reason": "本轮未执行原验证步骤，无法确认修复",
            }
            for step in state["baseline"]["steps"]
        ]

    previous_baseline = state["baseline"]
    baseline, baseline_reset = _resolve_baseline(
        state, steps, vuln.get("baseline"), at, fix_evidence,
    )
    new_issue = False
    previous_ticket = None
    linked_ticket = None

    comparison, failures = _compare(baseline, steps)
    if not steps:
        failures = [{
            "step": None,
            "reason": "本轮没有验证步骤，无法确认修复",
            "expected": True,
            "current": None,
            "evidence": [],
            "evidence_before": list(baseline.get("evidence", [])),
        }]

    # 已关闭的工单再次复现：按新问题处理，新工单关联原工单，并重新建立基线。
    if state["status"] == CLOSED and steps and failures:
        new_issue = True
        previous_ticket = state["ticket"]
        linked_ticket = state["origin_ticket"]
        state["issue"] += 1
        state["issue_round"] = 0
        state["status"] = OPEN
        state["baseline_seq"] += 1
        baseline = _build_baseline(
            vuln_id, state["issue"], steps, at, "reappeared_after_close",
            state["baseline_seq"], None, fix_evidence,
        )
        baseline_reset = True
        comparison, failures = _compare(baseline, steps)

    diff, diff_summary, changed = _diff(state["last_steps"], steps)
    passed = bool(steps) and not failures
    result = PASS if passed else FAIL

    if new_issue:
        ticket_status = OPEN
    elif passed:
        ticket_status = CLOSED
    else:
        ticket_status = state["status"]   # 未通过的保持原状

    ticket = _issue_ticket(state["origin_ticket"], state["issue"])
    evidence = _dedup([link for step in steps for link in step["evidence"]])

    _seq += 1
    record = {
        "vuln": vuln_id,
        "project": state["project"],
        "ticket": ticket,
        "linked_ticket": linked_ticket,
        "previous_ticket": previous_ticket,
        "issue": state["issue"],
        "new_issue": new_issue,
        "reappeared_after_close": new_issue,
        "round": state["round"] + 1,
        "issue_round": state["issue_round"] + 1,
        "seq": _seq,
        "at": at,
        "result": result,
        "passed": passed,
        "step_count": len(steps),
        "step_names": _step_names(steps),
        "steps": copy.deepcopy(steps),
        "baseline": copy.deepcopy(baseline),
        "baseline_reset": baseline_reset,
        "step_changes": _step_changes(previous_baseline, _step_names(steps)),
        "comparison": copy.deepcopy(comparison),
        "failures": copy.deepcopy(failures),
        "diff": diff,
        "diff_summary": diff_summary,
        "changed": changed,
        "closed": ticket_status == CLOSED,
        "ticket_status": ticket_status,
        "previous_result": state["previous_result"],
        "evidence": evidence,
        "evidence_before": list(baseline.get("evidence", [])),
    }

    state["round"] += 1
    state["issue_round"] += 1
    state["ticket"] = ticket
    state["status"] = ticket_status
    state["baseline"] = baseline
    state["last_steps"] = [{"name": step["name"], "ok": step["ok"]} for step in steps]
    state["previous_result"] = result

    _records.append(record)
    return copy.deepcopy(record)


def _rows(vuln, project):
    if vuln is not None and not isinstance(vuln, str):
        raise RetestError("vuln 需要字符串")
    if project is not None and not isinstance(project, str):
        raise RetestError("project 需要字符串")
    rows = [
        row for row in _records
        if (vuln is None or row["vuln"] == vuln)
        and (project is None or row["project"] == project)
    ]
    rows.sort(key=lambda row: (row["at"], row["seq"]))
    return copy.deepcopy(rows)


def _vuln_summary(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["vuln"], []).append(row)
    out = []
    for vuln_id, items in grouped.items():
        latest = items[-1]
        out.append({
            "vuln": vuln_id,
            "project": latest["project"],
            "rounds": len(items),
            "issue": latest["issue"],
            "rounds_in_issue": latest["issue_round"],
            "result": latest["result"],
            "closed": latest["closed"],
            "ticket": latest["ticket"],
            "ticket_status": latest["ticket_status"],
            "linked_ticket": latest["linked_ticket"],
            "new_issue": latest["new_issue"],
            "last_at": latest["at"],
            "failures": copy.deepcopy(latest["failures"]),
            "evidence": copy.deepcopy(latest["evidence"]),
        })
    out.sort(key=lambda item: (item["vuln"], item["last_at"]))
    return out


def _project_summary(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["project"], {}).setdefault(row["vuln"], []).append(row)
    out = []
    for project, vulns in grouped.items():
        latest = {vuln_id: items[-1] for vuln_id, items in vulns.items()}
        out.append({
            "project": project,
            "count": sum(len(items) for items in vulns.values()),
            "vulns": sorted(vulns),
            "open": sum(1 for row in latest.values() if row["ticket_status"] == OPEN),
            "closed": sum(1 for row in latest.values() if row["ticket_status"] == CLOSED),
            "last_at": max(row["at"] for items in vulns.values() for row in items),
        })
    out.sort(key=lambda item: (item["project"] is not None, item["project"] or ""))
    return out


def history(vuln=None, project=None):
    """按漏洞或项目查看复测记录。

    记录按时间先后排列；``vulns`` 给出每个漏洞的最新结论，``projects`` 给出
    项目维度的汇总（轮次、未关闭数和已关闭数）。
    """
    rows = _rows(vuln, project)
    vulns = _vuln_summary(rows)
    latest = rows[-1] if rows else None
    return {
        "vuln": vuln,
        "project": project,
        "count": len(rows),
        "records": rows,
        "vulns": vulns,
        "projects": _project_summary(rows),
        "latest": copy.deepcopy(latest),
        "closed": latest["closed"] if latest else None,
        "ticket_status": latest["ticket_status"] if latest else None,
        "result": latest["result"] if latest else None,
    }
