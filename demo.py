import threading
import time

from accountrefresh import account_status, audit_records, configure, refresh, report

# 小基数退避，让演示在秒级跑完；连续失败 3 次即隔离。
configure(backoff_base=0.1, backoff_factor=2, backoff_cap=1.0,
          isolate_threshold=3)

attempts = {"flaky": 0, "locked": 0}
gate = threading.Event()


def backend(account):
    attempts[account] += 1
    if account == "locked":
        raise RuntimeError("风控拒绝 %s 第%d次" % (account, attempts[account]))
    if attempts["flaky"] == 1:
        gate.wait(2.0)  # 让并发请求都进入等待，展示单飞
        raise RuntimeError("首次刷新被风控拒绝")
    return {"account": account, "token": "%s-token-%d" % (account, attempts[account]),
            "expires_in": 100}


configure(backend=backend)




print("== 同账号并发 6 次刷新（单飞）==")
threads = [threading.Thread(target=refresh, args=("flaky",)) for _ in range(6)]
for thread in threads:
    thread.start()
time.sleep(0.05)
gate.set()
for thread in threads:
    thread.join()
print(account_status("flaky"))

print("\n== 等退避窗口过去后重试，直到恢复 ==")
for _ in range(4):
    result = refresh("flaky")
    print({key: result.get(key) for key in ("ok", "backoff", "next_try", "token")})
    time.sleep(0.15)

print("\n== 另一账号连续失败进入隔离 ==")
for _ in range(3):
    refresh("locked", force=True)  # 演示用 force 累计失败，跳过退避等待
print(account_status("locked"))
configure(backend=lambda account: {"token": "recovered", "expires_in": 100})
print("恢复后:", refresh("locked", force=True))

print("\n== 汇总与审计 ==")
print(report())
print("audit events:", sorted({record["event"] for record in audit_records()}))
