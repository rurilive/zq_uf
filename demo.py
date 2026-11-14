"""演示：一个任务命中 429 后，同站点共享退避与阶梯恢复的全过程。

用注入的虚拟时钟推进时间，无需真实等待；状态写在临时目录，
不污染默认的 ~/.cache/crawlbackoff。
"""

import tempfile

from crawlbackoff import backoff, budget, signal

SITE = "shop"


def show(tag):
    view = budget(SITE)
    print(
        "%-28s phase=%-8s level=%s remaining=%2s/%2s 窗口剩余=%6.1fs 连续成功=%s"
        % (
            tag,
            view["phase"],
            view["level"],
            view["remaining"],
            view["budget"],
            view["window_remaining"],
            view["consecutive_successes"],
        )
    )


def main():
    clock = backoff.time.time()
    fake_now = [clock]
    backoff._now = lambda: fake_now[0]

    with tempfile.TemporaryDirectory() as tmp:
        backoff.set_state_dir(tmp)

        print("== 正常采集，满速预算 ==")
        signal(SITE, 200)
        show("任务A 200")

        print("\n== 任务A 命中 429：全站进入退避，任务B 同步降速 ==")
        signal(SITE, 429)
        show("任务A 429")
        show("任务B 查预算(共享)")

        print("\n== 退避窗口内：只放行试探流量，成功不累计恢复 ==")
        signal(SITE, 200)
        show("任务A 200(试探)")

        print("\n== 窗口结束：进入恢复阶梯第一级，预算 3 ==")
        fake_now[0] += backoff.WINDOW_BASE + 1
        show("窗口到期")

        print("\n== 第一级连续成功 3 次 -> 第二级，预算 6 ==")
        for i in range(backoff.SUCCESSES_PER_STEP):
            signal(SITE, 200)
        show("升级 level 2")

        print("\n== 第二级再连续成功 3 次 -> 回满速，预算 10 ==")
        for i in range(backoff.SUCCESSES_PER_STEP):
            signal(SITE, 200)
        show("恢复满速")

        print("\n最终快照:", budget(SITE))


if __name__ == "__main__":
    main()
