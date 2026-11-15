"""命令行查询预算账本。

用法::

    python3 -m jobbudget3 status          # 所有任务
    python3 -m jobbudget3 status JOB      # 单个任务
    python3 -m jobbudget3 actions [JOB]   # 降级/追加/恢复动作
    python3 -m jobbudget3 shadow          # 影子期报告
"""

import json
import sys

from .budget import BudgetManager


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "status"
    manager = BudgetManager()

    if command == "status":
        _print(manager.status(argv[1] if len(argv) > 1 else None))
        return 0
    if command == "actions":
        _print(manager.actions(argv[1] if len(argv) > 1 else None))
        return 0
    if command == "shadow":
        _print(manager.shadow_report())
        return 0

    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
