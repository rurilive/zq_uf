import threading
import time

from accountrefresh import audit_log, get_token, stats

print(get_token("acc-1"))
print(get_token("acc-1"))
print(stats())

# 第二段演示：并发单飞 + 刷新失败退避隔离
import importlib

m = importlib.import_module("accountrefresh.refresh")

m._reset()
m.TOKEN_TTL = 60.0
m.BACKOFF_BASE = 0.1
m.BACKOFF_MAX = 0.4

calls = {"ok": 0, "bad": 0}


def source(account):
    if account == "acc-bad":
        calls["bad"] += 1
        raise RuntimeError("risk control locked")
    calls["ok"] += 1
    time.sleep(0.05)
    return {"token": "tok-%d" % calls["ok"], "expires_in": 60.0}


m._token_source = source

results = []
threads = [threading.Thread(target=lambda: results.append(get_token("acc-ok")["token"]))
           for _ in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("acc-ok 并发 5 个请求，实际刷新 %d 次: %s" % (calls["ok"], results))

for i in range(3):
    try:
        get_token("acc-bad")
    except RuntimeError as exc:
        print("acc-bad 第 %d 次失败: %s" % (i + 1, exc))
    time.sleep(0.5)

print("stats:", stats())
print("audit:", audit_log())
