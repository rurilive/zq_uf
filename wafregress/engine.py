"""回归引擎与模块级入口。

Engine 持有用例集和结果留存，串起：规则校验 -> 变更影响分析 ->
逐条判定 -> 留存 -> 报告/对比。模块级函数（regress/report/...）
都委托给一个可替换的默认引擎，configure() 可切到文件留存。
"""
from datetime import datetime, timezone
from typing import List, Optional

from .cases import group_by_biz, load_case_files
from .errors import (
    DuplicateRuleId,
    NoBaselineRun,
    RunNotFound,
    WafValidationError,
)
from .impact import affected_cases, summarize_by_biz
from .models import Case, Rule, evaluate
from .report import build_report, compare_records
from .store import FileStore, MemoryStore

# 内置用例集：覆盖 search/login/pay 三条业务线、攻击与放行两侧。
_BUILTIN_CASES = [
    {"id": "c1", "biz": "search", "scenario": "query",
     "request": "/search?q=union select 1", "expect": "block", "expect_rule": "r1"},
    {"id": "c2", "biz": "search", "scenario": "query",
     "request": "/search?q=laptop", "expect": "pass"},
    {"id": "c7", "biz": "search", "scenario": "query",
     "request": "/search?q=coffee table", "expect": "pass"},
    {"id": "c3", "biz": "login", "scenario": "login_form",
     "request": "/login?user=admin'--", "expect": "block", "expect_rule": "r3"},
    {"id": "c4", "biz": "login", "scenario": "login_form",
     "request": "/login?user=alice", "expect": "pass"},
    {"id": "c5", "biz": "pay", "scenario": "note",
     "request": "/pay?note=<script>alert(1)</script>", "expect": "block",
     "expect_rule": "r2"},
    {"id": "c6", "biz": "pay", "scenario": "note",
     "request": "/pay?note=thanks", "expect": "pass"},
    {"id": "c8", "biz": "pay", "scenario": "nickname",
     "request": "/pay?nickname=<coolkid>", "expect": "pass"},
    {"id": "c9", "biz": "pay", "scenario": "note",
     "request": "/pay?note=<invoice>", "expect": "pass"},
]


def _validate_rules(rules) -> List[Rule]:
    if not isinstance(rules, list):
        raise WafValidationError("rules must be a list")
    parsed = []
    seen = set()
    for item in rules:
        rule = Rule.from_dict(item)
        if rule.id in seen:
            raise DuplicateRuleId(rule.id)
        seen.add(rule.id)
        parsed.append(rule)
    return parsed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Engine:
    """一次完整回归能力的载体：用例集 + 规则快照 + 历史留存。"""

    def __init__(self, store=None, cases: Optional[List[Case]] = None):
        self.store = store if store is not None else MemoryStore()
        self.cases: List[Case] = (
            list(cases) if cases is not None else [Case.from_dict(item) for item in _BUILTIN_CASES]
        )

    # ------------------------------------------------------------------ 用例
    def load_cases(self, paths) -> List[Case]:
        """从按业务线维护的文件/目录加载用例，替换当前用例集。"""
        self.cases = load_case_files(paths)
        return list(self.cases)

    def cases_by_biz(self) -> dict:
        return group_by_biz(self.cases)

    def reset(self) -> None:
        """清空历史回归结果（保留用例集）。"""
        self.store.clear()

    def list_runs(self) -> List[dict]:
        return self.store.list_runs()

    # ------------------------------------------------------------- 影响分析
    def _baseline(self) -> Optional[dict]:
        return self.store.latest()

    def impact(self, rules, biz: Optional[str] = None) -> dict:
        """执行前评估：相对上一轮规则，本轮变更会波及哪些用例。"""
        parsed = _validate_rules(rules)
        baseline = self._baseline()
        if baseline is None:
            raise NoBaselineRun()
        old_rules = [Rule.from_dict(item) for item in baseline["rules"]]
        ids, by_rule, diff = affected_cases(self.cases, old_rules, parsed, biz=biz)
        return {
            "rule_changes": diff,
            "affected_cases": ids,
            "by_rule": by_rule,
            "by_biz": summarize_by_biz(self.cases, ids),
        }

    # ----------------------------------------------------------------- 回归
    def regress(self, rules, only_affected: bool = False,
                biz: Optional[str] = None, run_id: Optional[int] = None) -> dict:
        """跑一轮回归并留存。

        only_affected=True 时只跑相对上一轮受规则变更影响的用例；
        biz 给定则只跑该业务线的用例。返回汇总计数与本轮影响范围。
        """
        parsed = _validate_rules(rules)
        baseline = self._baseline()
        old_rules = (
            [Rule.from_dict(item) for item in baseline["rules"]]
            if baseline is not None
            else []
        )

        impact_info = None
        affected_ids = []
        if baseline is not None:
            affected_ids, by_rule, diff = affected_cases(
                self.cases, old_rules, parsed, biz=biz
            )
            impact_info = {
                "rule_changes": diff,
                "affected_cases": affected_ids,
                "by_rule": by_rule,
                "by_biz": summarize_by_biz(self.cases, affected_ids),
            }
        elif only_affected:
            raise NoBaselineRun()

        run_cases = [case for case in self.cases if biz is None or case.biz == biz]
        if only_affected:
            id_set = set(affected_ids)
            run_cases = [case for case in run_cases if case.id in id_set]

        results = [evaluate(case, parsed).to_dict() for case in run_cases]
        new_run_id = run_id if run_id is not None else self.store.next_run_id()
        record = {
            "run_id": new_run_id,
            "timestamp": _now_iso(),
            "biz": biz,
            "only_affected": bool(only_affected),
            "baseline_run_id": baseline["run_id"] if baseline else None,
            "rules": [rule.to_dict() for rule in parsed],
            "cases": [case.to_dict() for case in run_cases],
            "results": results,
            "impact": impact_info,
        }
        # 汇总先算好再留存，保证落盘快照自包含、重启后索引仍可直接展示。
        full = build_report(record)
        record["summary"] = full["summary"]
        self.store.save_run(record)
        return {
            "run_id": new_run_id,
            "timestamp": record["timestamp"],
            "biz": biz,
            "only_affected": bool(only_affected),
            "rules": len(parsed),
            "cases": len(run_cases),
            "blocked": full["summary"]["blocked"],
            "false_positive": full["summary"]["false_positive"],
            "missed": full["summary"]["missed"],
            "passed": full["summary"]["passed"],
            "impact": self._impact_summary(impact_info),
        }

    @staticmethod
    def _impact_summary(impact_info):
        if impact_info is None:
            return None
        changes = impact_info["rule_changes"]
        return {
            "changed_rules": {
                "added": changes["added"],
                "removed": changes["removed"],
                "modified": [item["rule"] for item in changes["modified"]],
            },
            "affected_cases": impact_info["affected_cases"],
            "affected_count": len(impact_info["affected_cases"]),
            "by_biz": impact_info["by_biz"],
        }

    # ----------------------------------------------------------------- 报告
    def report(self, run_id: Optional[int] = None) -> dict:
        """查看某轮完整结果；默认最近一轮，未跑过时返回空报告骨架。"""
        if run_id is None:
            record = self._baseline()
            if record is None:
                return self._empty_report()
        else:
            record = self.store.get_run(run_id)
            if record is None:
                raise RunNotFound(run_id)
        return build_report(record)

    @staticmethod
    def _empty_report() -> dict:
        return {
            "run_id": None,
            "timestamp": None,
            "biz": None,
            "only_affected": False,
            "baseline_run_id": None,
            "rules": 0,
            "summary": {
                "total": 0, "blocked": 0, "false_positive": 0,
                "missed": 0, "passed": 0,
            },
            "blocked": [],
            "false_positives": {"total": 0, "by_scenario": {}},
            "missed": [],
            "passed": [],
            "by_rule": {},
            "unattributed_missed": [],
            "by_biz": {},
            "impact": None,
        }

    def compare(self, base_run_id: Optional[int] = None,
                target_run_id: Optional[int] = None) -> dict:
        """对比两轮结果；默认拿最近两轮（改动前 vs 改动后）。"""
        runs = self.list_runs()
        if len(runs) < 2 and (base_run_id is None or target_run_id is None):
            raise NoBaselineRun()

        if target_run_id is None:
            target_run_id = runs[-1]["run_id"]
        if base_run_id is None:
            base_run_id = runs[-2]["run_id"]

        base = self.store.get_run(base_run_id)
        target = self.store.get_run(target_run_id)
        if base is None:
            raise RunNotFound(base_run_id)
        if target is None:
            raise RunNotFound(target_run_id)
        return compare_records(base, target)


# ---------------------------------------------------------------- 默认引擎
_default_engine = Engine()


def configure(store_path: Optional[str] = None, store=None,
              cases: Optional[List[Case]] = None) -> Engine:
    """替换模块级默认引擎。

    store_path: 启用文件留存的目录；store: 直接给自定义 Store；
    都不给则回到纯内存引擎。
    """
    global _default_engine
    if store is not None and store_path is not None:
        raise WafValidationError("pass either store or store_path, not both")
    if store_path is not None:
        store = FileStore(store_path)
    _default_engine = Engine(store=store, cases=cases)
    return _default_engine


def get_default_engine() -> Engine:
    return _default_engine


def regress(rules, only_affected: bool = False, biz: Optional[str] = None,
            run_id: Optional[int] = None) -> dict:
    return _default_engine.regress(
        rules, only_affected=only_affected, biz=biz, run_id=run_id
    )


def report(run_id: Optional[int] = None) -> dict:
    return _default_engine.report(run_id)


def compare(base_run_id: Optional[int] = None,
            target_run_id: Optional[int] = None) -> dict:
    return _default_engine.compare(base_run_id, target_run_id)


def impact(rules, biz: Optional[str] = None) -> dict:
    return _default_engine.impact(rules, biz=biz)


def reset() -> None:
    _default_engine.reset()


def load_cases(paths) -> List[Case]:
    return _default_engine.load_cases(paths)


def list_runs() -> List[dict]:
    return _default_engine.list_runs()
