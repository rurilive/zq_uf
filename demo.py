"""演示：站点故障被隔离，恢复后经低频探测解除，观察期满后放量。"""

import time

import sitequarantine as sq


class FakeSite:
    """模拟站点：前两次探测仍故障，之后恢复。"""

    def __init__(self, fail_times):
        self.left = fail_times

    def __call__(self, host):
        if self.left > 0:
            self.left -= 1
            return False
        return True


def wait_for(out):
    """睡到下一次允许探测的时间。"""
    due = [time.time()]
    for key in ("until", "next_probe_at", "observation_until"):
        if out.get(key):
            due.append(out[key])
    time.sleep(max(0.0, max(due) - time.time()) + 0.02)


def main():
    sq.reset()
    sq.configure(ttl=0.1, probe_interval=0.05, release_after=2,
                 observation_window=0.2, prober=FakeSite(fail_times=2))

    print("隔离:", sq.quarantine("shop", "http 503"))

    while True:
        out = sq.probe("shop")
        print("探测:", out)
        if out["verdict"] in ("released", "open"):
            break
        wait_for(out)

    wait_for(out)  # 等观察期满
    print("放量:", sq.probe("shop"))

    print("手工覆盖:", sq.override("mall", "isolate", operator="ops", note="演练"))
    print("统计:", sq.stats())
    print("审计:", sq.audit())
    sq.reset()


if __name__ == "__main__":
    main()
