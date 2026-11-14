import time

from fieldpolicy import audit_log, grant, resolve, set_field, write_out
from fieldpolicy import policy

policy._reset()

# 字段层收紧：orders.phone 脱敏
set_field("orders", "phone", True)
print(resolve("orders", "phone"))

# 豁免过期演示：until 在过去，登记即失效并审计
grant("orders", "phone", time.time() - 1, approver="ops-admin")
print(resolve("orders", "phone"))
print(write_out("orders", {"phone": "13800000000", "city": "sz"}))
print(audit_log())
