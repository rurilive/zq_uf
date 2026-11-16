"""按崩溃原因归并模糊测试样本。

归并流程：

1. 每条崩溃先做归一化（见 :mod:`crashdedupe.normalize`），拿到与具体输入
   无关的指纹：归一化栈、出错指令、寄存器分类、故障地址分类、信号；
2. 用信号/故障点/访问类型等硬条件分桶（``cause_key``），桶内再按
   “栈相似 + 指令相似 + 寄存器分类一致”综合打分，命中阈值才并入已有
   条目，否则新开一条——栈相似但出错点不同的问题不会被并掉；
3. 条目保留全部样本编号，选一份代表性最好的样本用于复现；
4. 已关闭条目再次收到匹配样本时自动重新打开，记录回归版本；关闭状态下
   不会静默吸纳任何样本。

入口是 :func:`triage` 和 :func:`report`，人工可再 :func:`merge_issues` /
:func:`split_issue`。
"""

import copy

from .normalize import (
    cause_key,
    compatible_cause_keys,
    crash_fingerprint,
    insn_similarity,
    stack_similarity,
)
from .store import IssueStore

# 综合相似度达到该分才并入已有条目。
MATCH_SCORE_THRESHOLD = 0.7
# 单维度的硬门槛：栈太不像或指令完全不像，总分再高也不并。
STACK_GATE = 0.5
INSN_GATE = 0.5

_store = IssueStore()


# ---------------------------------------------------------------- 存储管理

def use_store(path=None):
    """切换后端：传路径用 JSON 持久化，不传重置为纯内存存储。"""
    global _store
    _store = IssueStore(path)
    return _store


def reset_store(path=None):
    """清空全部条目和样本记录，并（可选）切换到新的存储路径。"""
    global _store
    _store = IssueStore(path)
    return _store


def list_issues(status=None):
    """返回条目列表的深拷贝，status 可取 open/closed/regressed 过滤。"""
    issues = _store.issues
    if status:
        issues = [item for item in issues if item["status"] == status]
    return copy.deepcopy(issues)


def get_issue(issue_id):
    issue = _store.get(issue_id)
    return copy.deepcopy(issue) if issue else None


# ---------------------------------------------------------------- 匹配逻辑

def _registers_compatible(left_regs, right_regs):
    """故障寄存器必须同源：集合一致，且同名字段分类一致。

    出错点用的寻址寄存器不同（rax vs rbx），说明是另一条指令/另一个
    结构体字段，即使栈相似也不算同一原因；两侧都无寄存器信息时
    不否决，交给栈和指令维度裁决。
    """
    left_set = set(left_regs)
    right_set = set(right_regs)
    if left_set or right_set:
        if left_set != right_set:
            return False
    for name in left_set & right_set:
        if left_regs[name] != right_regs[name]:
            return False
    return True


def match_score(fp, baseline):
    """新样本指纹与条目基线指纹的匹配分（0~1）及各维度明细。"""
    stack_score = stack_similarity(fp["stack"], baseline["stack"])
    raw_insn = insn_similarity(fp["insn"], baseline["insn"])
    # 双方都无指令信息时按中立（0.5）计分，既不帮忙也不否决。
    insn_score = 0.5 if raw_insn is None else raw_insn
    regs_ok = _registers_compatible(fp["regs"], baseline["regs"])
    signal_ok = fp["signal"] == baseline["signal"]
    fault_ok = fp["fault_class"] == baseline["fault_class"]

    score = 0.45 * stack_score + 0.40 * insn_score + 0.15 * int(regs_ok)
    detail = {
        "stack_similarity": round(stack_score, 3),
        "insn_similarity": round(insn_score, 3),
        "insn_unknown": raw_insn is None,
        "registers_compatible": regs_ok,
        "signal_match": signal_ok,
        "fault_class_match": fault_ok,
    }
    return score, detail


def _is_same_cause(fp, baseline):
    """栈相似但出错点不同不算同因；硬门槛 + 综合分都要过。"""
    score, detail = match_score(fp, baseline)
    if detail["stack_similarity"] < STACK_GATE:
        return False, score, detail
    # 指令信息双方都缺省时不做指令硬裁决；只有一侧缺失或确实不像才否决。
    if not detail["insn_unknown"] and detail["insn_similarity"] < INSN_GATE:
        return False, score, detail
    if not detail["registers_compatible"]:
        return False, score, detail
    # 两侧故障地址都明确但类别不同才是矛盾；unknown 只是信息缺失，
    # 不否决，交由栈与指令维度裁决。
    if not detail["fault_class_match"] and "unknown" not in (
        fp["fault_class"],
        baseline["fault_class"],
    ):
        return False, score, detail
    return score >= MATCH_SCORE_THRESHOLD, score, detail


def _to_jsonable(value):
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


def _representative_score(record):
    """代表样本评分：故障信息越完整、归一化栈越深，越适合复现。"""
    crash = record["crash"]
    fp = record["fingerprint"]
    score = 0
    if crash.get("fault_addr") is not None:
        score += 2
    if fp["insn"] is not None:
        score += 2
    if fp["regs"]:
        score += 1
    score += min(len(fp["stack"]), 8)
    # 同分让先到的样本稳定当选，用负序号作为次序分。
    return score, -record.get("seq", 0)


def _pick_representative(sample_ids):
    return max(sample_ids, key=lambda sid: _representative_score(_store.records[sid]))


def _store_record(sample_id, crash, fp):
    """样本记录在判定前入库，代表样本评分随时可查。"""
    _store.records[sample_id] = {
        "crash": crash,
        "fingerprint": _to_jsonable(fp),
        "seq": len(_store.records),
    }


def _new_issue(fp, crash, key):
    issue_id = _store.next_id()
    issue = {
        "issue_id": issue_id,
        "target": fp["target"],
        "status": "open",
        "samples": [crash["id"]],
        "count": 1,
        "representative": crash["id"],
        "signals": [fp["signal"]],
        "cause_key": list(key),
        "extra_keys": [],
        "baseline": _to_jsonable(fp),
        "match_basis": {
            "rule": "new_cause",
            "reason": "无匹配条目：硬条件分桶 %s 内未达相似度阈值" % (key,),
            "dimensions": [
                "normalized_stack", "faulting_insn", "register_classes",
                "fault_addr_class", "signal",
            ],
            "last_match": None,
        },
        "history": [
            {"action": "created", "sample": crash["id"], "version": None}
        ],
        "fixed_version": None,
        "regression_versions": [],
    }
    _store.add(issue)
    return issue


def _attach(issue, fp, crash, match_detail, score, version):
    """把样本并入已有条目；条目处于关闭状态时先重新打开。"""
    sample_id = crash["id"]
    reopened = False
    if issue["status"] == "closed":
        issue["status"] = "regressed"
        issue["regression_versions"].append(version)
        issue["history"].append(
            {
                "action": "reopened",
                "sample": sample_id,
                "version": version,
                "fixed_version": issue.get("fixed_version"),
            }
        )
        reopened = True

    issue["samples"].append(sample_id)
    issue["count"] = len(issue["samples"])
    if fp["signal"] not in issue["signals"]:
        issue["signals"].append(fp["signal"])
    issue["match_basis"]["last_match"] = {
        "sample": sample_id,
        "score": round(score, 3),
        "detail": match_detail,
        "version": version,
        "reopened": reopened,
    }
    issue["history"].append(
        {"action": "merged_in", "sample": sample_id, "version": version}
    )

    _store.index_sample(sample_id, issue["issue_id"])
    if _representative_score(_store.records[sample_id]) > _representative_score(
        _store.records[issue["representative"]]
    ):
        issue["representative"] = sample_id


# ---------------------------------------------------------------- 入口

def triage(crashes, version=None):
    """归并一批崩溃样本，返回受影响条目与本批统计。

    同一原因（归一化栈 + 出错指令 + 寄存器分类 + 故障地址分类 + 信号
    匹配）的样本并入同一条目，不同原因即使栈相似也新开条目。
    ``version`` 标注本轮构建版本，关闭条目回归时据此记录回归版本。
    """
    if not isinstance(crashes, list):
        raise ValueError("crashes must be a list")

    touched = []
    dedup_new = 0
    for crash in crashes:
        if not isinstance(crash, dict) or "id" not in crash:
            raise ValueError("crash needs id")
        sample_id = crash["id"]
        existing = _store.find_by_sample(sample_id)
        if existing is not None:
            # 同一样本重复投递不重复计数。
            if existing not in touched:
                touched.append(existing)
            continue

        fp = crash_fingerprint(crash)
        key = cause_key(fp)
        _store_record(sample_id, crash, fp)

        matched = None
        for candidate in _store.candidates_for(compatible_cause_keys(key)):
            same, score, detail = _is_same_cause(fp, candidate["baseline"])
            if same:
                matched = (candidate, score, detail)
                break

        if matched is None:
            issue = _new_issue(fp, crash, key)
            touched.append(issue)
            dedup_new += 1
        else:
            issue, score, detail = matched
            _attach(issue, fp, crash, detail, score, version)
            if issue not in touched:
                touched.append(issue)

    _store.save()
    return {
        "issues": copy.deepcopy(touched),
        "total": len(crashes),
        "new_issues": dedup_new,
        "merged_samples": len(crashes) - dedup_new,
        "open_issues": sum(1 for item in _store.issues if item["status"] != "closed"),
    }


def report():
    """测试同学视角的汇总：真实问题数、按目标分布、回归与代表样本。"""
    by_target = {}
    open_issues = closed_issues = regressed = 0
    for issue in _store.issues:
        target = issue["target"] or "unknown"
        bucket = by_target.setdefault(
            target,
            {"samples": 0, "open": 0, "closed": 0, "regressed": 0},
        )
        bucket["samples"] += issue["count"]
        if issue["status"] == "closed":
            closed_issues += 1
            bucket["closed"] += 1
        else:
            open_issues += 1
            bucket["open"] += 1
            if issue["status"] == "regressed":
                regressed += 1
                bucket["regressed"] += 1

    return {
        "issues": len(_store.issues),
        "open_issues": open_issues,
        "closed_issues": closed_issues,
        "regressed_issues": regressed,
        "by_target": by_target,
        "representative": open_issues,
        "representatives": [
            {
                "issue_id": issue["issue_id"],
                "target": issue["target"],
                "representative": issue["representative"],
                "count": issue["count"],
                "status": issue["status"],
            }
            for issue in _store.issues
            if issue["status"] != "closed"
        ],
    }


# ---------------------------------------------------------------- 人工操作

def close_issue(issue_id, fixed_version):
    """标记条目已在 fixed_version 修复；关闭后不再接收新样本。"""
    issue = _store.get(issue_id)
    if issue is None:
        raise KeyError(issue_id)
    issue["status"] = "closed"
    issue["fixed_version"] = fixed_version
    issue["history"].append(
        {"action": "closed", "version": fixed_version}
    )
    _store.save()
    return copy.deepcopy(issue)


def merge_issues(issue_ids, reason="manual"):
    """人工把多个条目合成一条，全部样本编号与候选键都保留。"""
    if len(set(issue_ids)) < 2:
        raise ValueError("merge needs at least two distinct issue ids")
    primary = _store.get(issue_ids[0])
    if primary is None:
        raise KeyError(issue_ids[0])

    for other_id in issue_ids[1:]:
        other = _store.get(other_id)
        if other is None:
            raise KeyError(other_id)
        for sample_id in other["samples"]:
            primary["samples"].append(sample_id)
            _store.index_sample(sample_id, primary["issue_id"])
        if other["cause_key"] not in primary["extra_keys"]:
            primary["extra_keys"].append(other["cause_key"])
        for extra in other.get("extra_keys", []):
            if extra not in primary["extra_keys"]:
                primary["extra_keys"].append(extra)
        for signal in other["signals"]:
            if signal not in primary["signals"]:
                primary["signals"].append(signal)
        primary["history"].append(
            {
                "action": "manual_merged",
                "from": other["issue_id"],
                "reason": reason,
            }
        )
        _store.remove(other["issue_id"])

    primary["count"] = len(primary["samples"])
    primary["representative"] = _pick_representative(primary["samples"])
    primary["match_basis"]["rule"] = "manual_merge"
    _store.save()
    return copy.deepcopy(primary)


def split_issue(issue_id, sample_groups, reason="manual"):
    """人工把一个条目拆成多个条目。

    ``sample_groups`` 是样本编号的分组列表，每组生成一条新条目；
    原条目的样本必须被各组完整且不重不漏地覆盖。
    """
    issue = _store.get(issue_id)
    if issue is None:
        raise KeyError(issue_id)
    groups = [list(group) for group in sample_groups]
    if not groups or any(not group for group in groups):
        raise ValueError("sample_groups must be non-empty sample lists")
    covered = [sid for group in groups for sid in group]
    # 必须完整且不重不漏地覆盖原条目样本。
    if sorted(covered) != sorted(issue["samples"]) or len(set(covered)) != len(
        covered
    ):
        raise ValueError("sample_groups must partition the issue samples")

    _store.remove(issue_id)
    created = []
    for group in groups:
        first_fp = _store.records[group[0]]["fingerprint"]
        key = cause_key(first_fp)
        new_issue = {
            "issue_id": _store.next_id(),
            "target": issue["target"],
            "status": "open",
            "samples": group,
            "count": len(group),
            "representative": _pick_representative(group),
            "signals": sorted(
                {_store.records[sid]["fingerprint"]["signal"] for sid in group}
            ),
            "cause_key": list(key),
            "extra_keys": [],
            "baseline": first_fp,
            "match_basis": {
                "rule": "manual_split",
                "reason": reason,
                "split_from": issue_id,
                "dimensions": [
                    "normalized_stack", "faulting_insn", "register_classes",
                    "fault_addr_class", "signal",
                ],
                "last_match": None,
            },
            "history": [
                {
                    "action": "split_from",
                    "from": issue_id,
                    "sample": sid,
                    "version": None,
                }
                for sid in group
            ],
            "fixed_version": None,
            "regression_versions": [],
        }
        for sid in group:
            _store.index_sample(sid, new_issue["issue_id"])
        _store.add(new_issue)
        created.append(new_issue)

    _store.save()
    return copy.deepcopy(created)
