"""reconcheck 演示：两份有差异的数据，输出可定位的差异报告。"""
import json

from reconcheck import compare

LEFT = [
    {"id": 1, "amount": 100.00, "currency": "CNY"},
    {"id": 2, "amount": 200.00, "currency": "CNY"},
    {"id": 3, "amount": 300.005, "currency": "CNY"},  # 精度内的差异，不算错
    {"id": 4, "amount": 400.00, "currency": "USD"},   # 字段不一致
    {"id": 5, "amount": 500.00, "currency": "CNY"},  # 仅左有
    {"id": 6, "amount": 600.00, "currency": "CNY"},  # 命中例外规则，降级为提示
    {"id": 7, "amount": 700.00, "currency": "CNY"},  # 重复主键
    {"id": 7, "amount": 700.00, "currency": "CNY"},
]
RIGHT = [
    {"id": 1, "amount": 100.00, "currency": "CNY"},
    {"id": 2, "amount": 200.00, "currency": "CNY"},
    {"id": 3, "amount": 300.00, "currency": "CNY"},
    {"id": 4, "amount": 400.00, "currency": "CNY"},
    {"id": 6, "amount": 600.02, "currency": "CNY"},
    {"id": 8, "amount": 800.00, "currency": "CNY"},  # 仅右有
]

CONFIG = {
    "keys": ["id"],
    "tolerance": {"abs": 0.01, "rel": 1e-9},
    "exceptions": [{"keys": [6], "fields": ["amount"], "max_abs": 0.05}],
}

report = compare(LEFT, RIGHT, CONFIG)
print(json.dumps(report, ensure_ascii=False, indent=2))
