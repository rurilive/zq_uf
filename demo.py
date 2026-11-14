import importlib
import threading
import time

from accountrefresh import audit_log, get_token, report, stats

impl = importlib.import_module("accountrefresh.refresh")

print(get_token("acc-1"))
print(get_token("acc-1"))
print(stats())

# 第二段：并发刷新 + 失败退避隔离演示
impl._states.clear()
impl._counts.clear()
impl._reused.clear()
impl._audit.clear()
impl._token_ttl = 100.0
impl.BACKOFF_BASE = 0.2

fails = {"acc-bad": 10}  # acc-bad 的令牌来源持续失败


def source(account):
    if fails.get(account, 0):
        fails[account] -= 1
        raise RuntimeError("risk control")
    time.sleep(0.05)  # 模拟令牌端点耗时，让并发请求撞上在途刷新
    return "tok-" + account, 100.0


impl._token_source = source

# 三个账号各 3 个线程并发取令牌：同账号单飞，其余等待复用
threads = [threading.Thread(target=get_token, args=(name,))
           for name in ("acc-1", "acc-2", "acc-bad") for _ in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()

# acc-bad 连续失败，退避逐步拉长，达到阈值后被隔离
for _ in range(4):
    get_token("acc-bad")
    time.sleep(0.25)

print(report())
for row in audit_log():
    print(row)
