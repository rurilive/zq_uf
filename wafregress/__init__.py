"""WAF 规则回归工具。

对外入口：
- regress(rules, ...)  执行一轮回归，返回命中/误拦/漏拦/放行与变更影响
- report(run_id=...)   查看某轮结果（误拦按业务场景分组、按规则统计）
- compare(...)         对比两轮结果（规则改动前后）
- impact(rules, ...)   执行前评估变更影响范围
- load_cases(...)      从按业务线维护的用例目录/文件加载用例
- reset()              清空回归结果
- configure(...)       切换默认引擎（例如启用文件留存）
"""
from .engine import Engine
from .errors import CaseNotFound, DuplicateCaseId, RunNotFound, WafValidationError
from .models import Case, Rule
from .engine import get_default_engine, configure

# 模块级便捷入口，全部委托给默认引擎
from .engine import (
    regress,
    report,
    compare,
    impact,
    reset,
    load_cases,
    list_runs,
)

__all__ = [
    "regress",
    "report",
    "compare",
    "impact",
    "reset",
    "load_cases",
    "list_runs",
    "configure",
    "Engine",
    "Case",
    "Rule",
    "WafValidationError",
    "DuplicateCaseId",
    "RunNotFound",
    "CaseNotFound",
]
