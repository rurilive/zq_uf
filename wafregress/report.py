"""结果汇总与前后对比。

report 面向安全同学的查看习惯：
- 误拦（false_positive）按业务线/业务场景分组，附带原始请求，可直接复现；
- 漏拦（missed）保留触发请求与期望规则；
- 误拦、漏拦数量按规则统计；
- 同时给出按业务线的两侧统计。

compare 对比任意两轮：用例结果流转、新增/消除的误拦与漏拦、汇总差值。
"""
from typing import List, Optional

from .impact import diff_rules
from .models import (
    OUTCOME_BLOCKED,
    OUTCOME_FALSE_POSITIVE,
    OUTCOME_MISSED,
    OUTCOME_PASSED,
)

OUTCOMES = (
    OUTCOME_BLOCKED,
    OUTCOME_FALSE_POSITIVE,
    OUTCOME_MISSED,
    OUTCOME_PASSED,
)


def _fp_detail(result: dict) -> dict:
    """误拦用例的复现材料：原始请求 + 是哪条（哪些）规则拦的。"""
    return {
        "case": result["case_id"],
        "biz": result["biz"],
        "scenario": result["scenario"],
        "request": result["request"],
        "blocking_rule": result["blocking_rule"],
        "matched_rules": list(result["matched_rules"]),
    }


def _miss_detail(result: dict) -> dict:
    """漏拦用例保留触发请求，方便直接重放复现。"""
    return {
        "case": result["case_id"],
        "biz": result["biz"],
        "scenario": result["scenario"],
        "request": result["request"],
        "expect_rule": result.get("expect_rule"),
    }


def _group_false_positives(results: List[dict]):
    """误拦用例按 业务线 -> 业务场景 分组。"""
    grouped = {}
    for result in results:
        if result["outcome"] != OUTCOME_FALSE_POSITIVE:
            continue
        biz_group = grouped.setdefault(result["biz"], {})
        scenario_group = biz_group.setdefault(result["scenario"], [])
        scenario_group.append(_fp_detail(result))
    # 输出前排序，保证结果稳定可 diff
    return {
        biz: {
            scenario: sorted(items, key=lambda item: item["case"])
            for scenario, items in sorted(biz_group.items())
        }
        for biz, biz_group in sorted(grouped.items())
    }


def _aggregate_by_rule(results: List[dict], rules: List[dict]):
    """误拦/漏拦/正确拦截按规则统计。

    - blocked / false_positive 归因到实际触发拦截的 blocking_rule；
    - missed 按用例声明的 expect_rule 归因（期望哪条规则拦却没拦住）；
      未声明 expect_rule 的漏拦进入 unattributed_missed，不凭空分摊。
    """
    by_rule = {
        rule["id"]: {
            "pattern": rule["pattern"],
            "scope": rule.get("scope"),
            "blocked": 0,
            "false_positive": 0,
            "missed": 0,
        }
        for rule in rules
    }
    unattributed_missed = []
    for result in results:
        outcome = result["outcome"]
        if outcome in (OUTCOME_BLOCKED, OUTCOME_FALSE_POSITIVE):
            rule_id = result["blocking_rule"]
            entry = by_rule.setdefault(
                rule_id,
                {
                    "pattern": None,
                    "scope": None,
                    "blocked": 0,
                    "false_positive": 0,
                    "missed": 0,
                },
            )
            if outcome == OUTCOME_BLOCKED:
                entry["blocked"] += 1
            else:
                entry["false_positive"] += 1
        elif outcome == OUTCOME_MISSED:
            expect_rule = result.get("expect_rule")
            if expect_rule:
                entry = by_rule.setdefault(
                    expect_rule,
                    {
                        "pattern": None,
                        "scope": None,
                        "blocked": 0,
                        "false_positive": 0,
                        "missed": 0,
                    },
                )
                entry["missed"] += 1
            else:
                unattributed_missed.append(result["case_id"])
    return dict(sorted(by_rule.items())), sorted(unattributed_missed)


def _aggregate_by_biz(results: List[dict]):
    by_biz = {}
    for result in results:
        entry = by_biz.setdefault(
            result["biz"],
            {
                "total": 0,
                "blocked": 0,
                "false_positive": 0,
                "missed": 0,
                "passed": 0,
                "false_positive_cases": [],
                "missed_cases": [],
            },
        )
        entry["total"] += 1
        entry[result["outcome"]] += 1
        if result["outcome"] == OUTCOME_FALSE_POSITIVE:
            entry["false_positive_cases"].append(result["case_id"])
        elif result["outcome"] == OUTCOME_MISSED:
            entry["missed_cases"].append(result["case_id"])
    for entry in by_biz.values():
        entry["false_positive_cases"].sort()
        entry["missed_cases"].sort()
    return dict(sorted(by_biz.items()))


def _summary(results: List[dict]):
    summary = {"total": len(results)}
    for outcome in OUTCOMES:
        summary[outcome] = 0
    for result in results:
        summary[result["outcome"]] += 1
    return summary


def build_report(record: dict) -> dict:
    """根据一轮留存快照构建完整报告。"""
    results = record["results"]
    rules = record["rules"]
    by_rule, unattributed_missed = _aggregate_by_rule(results, rules)
    return {
        "run_id": record["run_id"],
        "timestamp": record["timestamp"],
        "biz": record.get("biz"),
        "only_affected": record.get("only_affected", False),
        "baseline_run_id": record.get("baseline_run_id"),
        "rules": len(rules),
        "cases": len(results),
        "summary": _summary(results),
        "blocked": sorted(
            (
                {
                    "case": result["case_id"],
                    "biz": result["biz"],
                    "rule": result["blocking_rule"],
                    "matched_rules": list(result["matched_rules"]),
                }
                for result in results
                if result["outcome"] == OUTCOME_BLOCKED
            ),
            key=lambda item: item["case"],
        ),
        "false_positives": {
            "total": sum(
                1 for result in results if result["outcome"] == OUTCOME_FALSE_POSITIVE
            ),
            "by_scenario": _group_false_positives(results),
        },
        "missed": sorted(
            (
                _miss_detail(result)
                for result in results
                if result["outcome"] == OUTCOME_MISSED
            ),
            key=lambda item: item["case"],
        ),
        "passed": sorted(
            result["case_id"]
            for result in results
            if result["outcome"] == OUTCOME_PASSED
        ),
        "by_rule": by_rule,
        "unattributed_missed": unattributed_missed,
        "by_biz": _aggregate_by_biz(results),
        "impact": record.get("impact"),
    }


def compare_records(base: dict, target: dict) -> dict:
    """对比两轮回归结果（通常 base=改动前，target=改动后）。"""
    base_map = {result["case_id"]: result for result in base["results"]}
    target_map = {result["case_id"]: result for result in target["results"]}

    transitions = []
    new_fp, resolved_fp, new_missed, resolved_missed = [], [], [], []
    for case_id in sorted(set(base_map) | set(target_map)):
        before = base_map.get(case_id)
        after = target_map.get(case_id)
        before_outcome = before["outcome"] if before else None
        after_outcome = after["outcome"] if after else None
        if before_outcome == after_outcome:
            continue
        transitions.append(
            {
                "case": case_id,
                "biz": (after or before)["biz"],
                "before": before_outcome,
                "after": after_outcome,
                "request": (after or before)["request"],
            }
        )
        if after_outcome == OUTCOME_FALSE_POSITIVE:
            new_fp.append(_fp_detail(after))
        if before_outcome == OUTCOME_FALSE_POSITIVE and after:
            resolved_fp.append(case_id)
        if after_outcome == OUTCOME_MISSED:
            new_missed.append(_miss_detail(after))
        if before_outcome == OUTCOME_MISSED and after:
            resolved_missed.append(case_id)

    base_summary = _summary(base["results"])
    target_summary = _summary(target["results"])
    summary_delta = {
        key: target_summary.get(key, 0) - base_summary.get(key, 0)
        for key in OUTCOMES
    }
    return {
        "base_run_id": base["run_id"],
        "target_run_id": target["run_id"],
        "rule_changes": diff_rules(
            _as_rules(base["rules"]), _as_rules(target["rules"])
        ),
        "summary_base": base_summary,
        "summary_target": target_summary,
        "summary_delta": summary_delta,
        "transitions": transitions,
        "new_false_positives": sorted(new_fp, key=lambda item: item["case"]),
        "resolved_false_positives": sorted(resolved_fp),
        "new_missed": sorted(new_missed, key=lambda item: item["case"]),
        "resolved_missed": sorted(resolved_missed),
    }


def _as_rules(rule_dicts: List[dict]):
    # 延迟导入避免 models -> report 的循环依赖
    from .models import Rule

    return [Rule.from_dict(item) for item in rule_dicts]
