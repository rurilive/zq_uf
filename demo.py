"""演示：并发单飞、失败退避和账号隔离。"""

import threading
import time

import accountrefresh
from accountrefresh import get_token, status


def main():
    accountrefresh.reset()
    accountrefresh.configure(refresh_ahead=5, backoff_base=0.5,
                             backoff_max=2.0, isolate_threshold=2)

    def issuer(account):
        time.sleep(0.05)
        if account == "acc-bad":
            raise RuntimeError("risk control")
        return {"token": "tok-%s-%d" % (account, time.time_ns()),
                "expires_in": 30}

    accountrefresh.configure(issuer=issuer)

    results = []
    threads = [threading.Thread(target=lambda: results.append(get_token("acc-1")))
               for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print("acc-1 并发 5 线程令牌一致:", len({r["token"] for r in results}) == 1)

    for _ in range(3):
        outcome = get_token("acc-bad")
        print("acc-bad 刷新失败:", outcome.get("error"),
              "下次可试:", outcome.get("next_try"))
        time.sleep(0.6)
    print("acc-2 不受 acc-bad 拖累:", get_token("acc-2")["token"] is not None)

    current = status()
    print("刷新次数:", current["refreshes"])
    print("等待复用:", current["reused"])
    print("退避中:", current["backoff"])
    print("隔离账号:", current["isolated"])
    print("调度权重:", current["weights"])
    print("审计条数:", len(accountrefresh.audit_log()))


if __name__ == "__main__":
    main()
