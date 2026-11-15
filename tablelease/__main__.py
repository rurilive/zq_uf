"""命令行查看租约状态：python3 -m tablelease [table]"""

import json
import sys

from . import audit_log, status

table = sys.argv[1] if len(sys.argv) > 1 else None
print(json.dumps({"leases": status(table), "audit": audit_log()},
                 ensure_ascii=False, indent=2))
