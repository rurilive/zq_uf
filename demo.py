"""模拟并发取令牌：acc-ok 正常，acc-flaky 前两次失败，acc-dead 持续失败。"""

import threading
import time

import importlib

import accountrefresh as ar

impl = importlib.import_module("accountrefresh.refresh")
from accountrefresh import get_token, report, set_issuer, stats

impl.BACKOFF_BASE_S = 0.2
impl.BACKOFF_MAX_S = 1.0
impl.ISOLATE_AFTER = 3
impl.ISOLATION_S = 5.0

_attempts = {}
_guard = threading.Lock()


def issuer(account):
    with _guard:
        _attempts[account] = _attempts.get(account, 0) + 1
        n = _attempts[account]
    time.sleep(0.05)  # 模拟签发耗时
    if account == "acc-dead":
        raise RuntimeError("签发持续失败")
    if account == "acc-flaky" and n <= 2:
        raise RuntimeError("临时风控")
    return {"token": "%s-tok-%d" % (account, n), "expires_in": 3600}


set_issuer(issuer)


def worker(account, rounds=8):
    for _ in range(rounds):
        get_token(account)
        time.sleep(0.12)


# 同一账号并发：单飞，其余等待复用
burst = [threading.Thread(target=get_token, args=("acc-ok",))
         for _ in range(5)]
for t in burst:
    t.start()
for t in burst:
    t.join()

# 多账号并发：互不阻塞，失败账号单独退避/隔离
threads = [threading.Thread(target=worker, args=(acc,))
           for acc in ("acc-ok", "acc-flaky", "acc-dead")]
for t in threads:
    t.start()
for t in threads:
    t.join()

rep = report()
print("刷新次数:", stats())
print("等待复用:", rep["waits"])
print("退避状态:", rep["backoff"])
print("隔离账号:", rep["isolated"])
print("调度权重:", rep["weights"])
print("审计样例:")
for entry in ar.audit_log()[-3:]:
    print(" ", entry)
