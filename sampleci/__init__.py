"""sampleci —— 可复现的分层采样与误差区间。

公开接口：
- sample / replay：分层采样与同计划重放（固定种子，比例与固定条数可混用）
- summarize / error：均值、分位数、置信区间；长尾层单独统计
- full_scan / compare：全量真值与采样估计对比，作为推广前的验收
- to_json / samples_to_csv / report_to_csv / write_files：结果导出
"""
from .sample import DEFAULT_SEED, normalize_plan, replay, sample
from .stats import DEFAULT_QUANTILES, error, summarize
from .validation import compare, full_scan
from .export import (
    report_to_csv,
    samples_to_csv,
    to_json,
    write_files,
)

__all__ = [
    "sample",
    "replay",
    "normalize_plan",
    "summarize",
    "error",
    "full_scan",
    "compare",
    "to_json",
    "samples_to_csv",
    "report_to_csv",
    "write_files",
    "DEFAULT_SEED",
    "DEFAULT_QUANTILES",
]
