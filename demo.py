import json
import time

from qualitygate import audit, check, waive
from qualitygate.gate import _reset

_reset()

# 豁免：一条生效中（有到期时间和原因），一条登记即过期，留痕但不生效。
waive("null_id", time.time() + 86400, "已知问题，owner 已签字，明天修复")
waive("legacy_code", time.time() - 3600, "历史豁免，已到期未清理")

batch = {
    "thresholds": {"warning_rows": 1000, "warning_ratio": 0.01},
    "rules": [
        {"name": "null_id", "failed": True, "level": "block", "failed_rows": 3},
        {"name": "dup_pk", "failed": True, "level": "block", "failed_rows": 7},
        {"name": "late_arrival", "failed": True, "level": "warning", "failed_rows": 50, "total_rows": 100000},
        {"name": "null_comment", "failed": True, "level": "warning", "failed_rows": 3000, "total_rows": 500000},
        {"name": "naming_tip", "failed": True, "level": "info", "failed_rows": 4},
        {"name": "range", "failed": False},
    ],
}

result = check(batch)
print("首次检查:", json.dumps(result, ensure_ascii=False, indent=2))
print("重复检查一致:", check(batch) == result)
print("豁免审计:", json.dumps(audit(), ensure_ascii=False, indent=2))
