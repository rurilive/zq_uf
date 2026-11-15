"""演示：按站点自适应限速。

慢站和快站各走各的速度；429 后立即降速冷却；冷却结束从低速重新开始；
额度不足时排队等待；调整过程可按站点回看与导出。
"""

import json

from rateadapt import acquire, export_records, feedback, records, set_clock, status
from rateadapt import rate as mod


class Clock:
    """演示用假时钟，避免真的等冷却时间。"""

    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def show(label, result):
    print("%-16s rate=%-4.2f remaining=%-2d cooldown=%4.1fs  %s"
          % (label, result["rate"], result["remaining"], result["cooldown_in"], result["reason"]))
    if result.get("hint"):
        print("                 -> %s" % result["hint"])


def main():
    clock = Clock()
    set_clock(clock, sleeper=clock.advance)
    mod.reset()

    print("== 慢站连续成功，逐步提速 ==")
    for _ in range(mod.SUCCESS_THRESHOLD):
        clock.advance(1.0)
        show("200", feedback("shop", 200, 0.9))

    print("\n== 快站同样成功，速度互不影响 ==")
    clock.advance(1.0)
    show("200 (api)", feedback("api", 200, 0.1))

    print("\n== shop 返回 429：立即降速并冷却，期间不放行 ==")
    show("429", feedback("shop", 429, 1.4))
    show("acquire", acquire("shop"))

    print("\n== 冷却结束后从低速重新开始 ==")
    clock.advance(mod.MAX_COOLDOWN + 0.1)
    show("acquire", acquire("shop"))

    print("\n== 额度不足时排队等待 ==")
    first = acquire("shop")
    show("acquire", first)
    feedback("shop", 200, 0.4)          # 回填结果，释放并发名额
    show("acquire(wait)", acquire("shop", wait=True, max_wait=3.0))

    print("\n== shop 最近一小时的调整过程 ==")
    for entry in records("shop"):
        print("  %s  %-9s %.2f -> %.2f  (%s)"
              % (entry["at"], entry["kind"], entry["prev_rate"], entry["rate"], entry["reason"]))

    print("\n== 按站点导出（JSON，最多显示 3 条）==")
    payload = json.loads(export_records("shop"))
    print(json.dumps({**payload, "records": payload["records"][:3]}, ensure_ascii=False, indent=2))

    print("\n== 值班视图 ==")
    print(json.dumps(status("shop"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
