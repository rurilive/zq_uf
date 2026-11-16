"""变更影响分析。

在真正发请求之前，先对比「上一轮规则快照」和「本轮规则」，
找出新增/删除/修改的规则，并静态推断哪些用例会受影响。
这样规则一改，执行前就能看到影响范围，再决定只跑受影响用例。
"""
from typing import Iterable, List, Optional

from .models import Case, Rule

CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_MODIFIED = "modified"


def diff_rules(old_rules: Iterable[Rule], new_rules: Iterable[Rule]):
    """对比两组规则，返回新增/删除/修改的规则 id 及明细。"""
    old_map = {rule.id: rule for rule in old_rules}
    new_map = {rule.id: rule for rule in new_rules}

    added, removed, modified = [], [], []
    for rule_id, rule in new_map.items():
        if rule_id not in old_map:
            added.append(rule_id)
        else:
            old = old_map[rule_id]
            if (
                old.pattern != rule.pattern
                or old.scope != rule.scope
                or old.description != rule.description
            ):
                modified.append(
                    {
                        "rule": rule_id,
                        "before": old.to_dict(),
                        "after": rule.to_dict(),
                    }
                )
    for rule_id in old_map:
        if rule_id not in new_map:
            removed.append(rule_id)

    return {
        "added": sorted(added),
        "removed": sorted(removed),
        "modified": sorted(modified, key=lambda item: item["rule"]),
    }


def _rule_touches_case(rule: Rule, case: Case) -> bool:
    """规则与用例是否相关：作用域匹配，且模式可能/确实出现在请求中。"""
    if not rule.applies_to(case.biz):
        return False
    # 子串匹配语义下，pattern 出现在请求里即相关；
    # 空串会命中所有请求，也按相关处理。
    return rule.pattern in case.request


def affected_cases(
    cases: List[Case],
    old_rules: List[Rule],
    new_rules: List[Rule],
    biz: Optional[str] = None,
):
    """根据规则变更推断受影响用例。

    - 新增规则：可能新拦/新放与它相关的用例；
    - 删除规则：原本靠它拦的用例可能变成漏拦；
    - 修改规则：模式或作用域变化，新旧规则各自相关的用例都纳入。
    返回 (受影响用例 id 列表, 按规则 id 的影响明细)。
    """
    diff = diff_rules(old_rules, new_rules)
    old_map = {rule.id: rule for rule in old_rules}
    new_map = {rule.id: rule for rule in new_rules}

    scoped_cases = [case for case in cases if biz is None or case.biz == biz]

    def _in_scope(rule: Optional[Rule]) -> bool:
        # 未做业务线过滤时所有规则都在分析范围内；
        # 过滤后，全局规则（无 scope）始终在范围内，
        # 带 scope 的规则只有作用域恰好覆盖该业务线才纳入。
        if rule is None or biz is None:
            return True
        return rule.scope is None or rule.scope == biz

    touched = set()
    by_rule = {}

    def _mark(rule_id: str, rule: Optional[Rule], change: str):
        if not _in_scope(rule):
            return
        ids = set()
        if rule is not None:
            for case in scoped_cases:
                if _rule_touches_case(rule, case):
                    ids.add(case.id)
        entry = by_rule.setdefault(
            rule_id, {"change": change, "affected_cases": set()}
        )
        entry["change"] = change
        entry["affected_cases"].update(ids)
        touched.update(ids)

    for rule_id in diff["added"]:
        _mark(rule_id, new_map[rule_id], CHANGE_ADDED)
    for rule_id in diff["removed"]:
        _mark(rule_id, old_map[rule_id], CHANGE_REMOVED)
    for item in diff["modified"]:
        rule_id = item["rule"]
        old_rule, new_rule = old_map[rule_id], new_map[rule_id]
        if not (_in_scope(old_rule) or _in_scope(new_rule)):
            continue
        before, after = set(), set()
        for case in scoped_cases:
            if _in_scope(old_rule) and _rule_touches_case(old_rule, case):
                before.add(case.id)
            if _in_scope(new_rule) and _rule_touches_case(new_rule, case):
                after.add(case.id)
        ids = before | after
        by_rule[rule_id] = {
            "change": CHANGE_MODIFIED,
            "affected_cases": ids,
            "before": item["before"],
            "after": item["after"],
        }
        touched.update(ids)

    by_rule_out = {}
    for rule_id in sorted(by_rule):
        entry = by_rule[rule_id]
        out = {
            "change": entry["change"],
            "affected_cases": sorted(entry["affected_cases"]),
        }
        if "before" in entry:
            out["before"] = entry["before"]
            out["after"] = entry["after"]
        by_rule_out[rule_id] = out

    return sorted(touched), by_rule_out, diff


def summarize_by_biz(cases: List[Case], case_ids: Iterable[str]):
    """把受影响用例按业务线计数，体现改动只波及对应业务线。"""
    id_set = set(case_ids)
    counts = {}
    for case in cases:
        if case.id in id_set:
            counts[case.biz] = counts.get(case.biz, 0) + 1
    return dict(sorted(counts.items()))
