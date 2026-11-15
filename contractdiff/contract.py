"""数据契约兼容性检查入口。

兼容性按读写方向分别判定：
- 读取方向：类型收紧（如 int -> short）、删除列、枚举删值算破坏；
- 写入方向：类型放宽（如 int -> long）、新增必填且无默认值、枚举加值算破坏；
- 删除列两个方向都算破坏；
- 默认值、枚举、必填单独标记，每条结论带方向、字段路径和建议。
"""

from .engine import check, RuleBook, DEFAULT_RULEBOOK  # noqa: F401

__all__ = ["check", "RuleBook", "DEFAULT_RULEBOOK"]
