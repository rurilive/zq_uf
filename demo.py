"""demo：两个站点混合流量下的独立额度、超额切换与对账。

直接运行：python3 demo.py
状态和对账单写在临时目录里，运行结束会打印目录路径，可自行查看。
"""

import json
import tempfile

from proxybudget import (
    configure,
    consume,
    remaining,
    daily_report,
    monthly_report,
    save_reports,
)


def show(title, payload):
    print("== %s ==" % title)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print()


def main():
    state_dir = tempfile.mkdtemp(prefix="proxybudget-demo-")
    print("状态目录: %s\n" % state_dir)

    # shop 小额度、两个出口；news 大额度、两个出口；其余站点走默认额度。
    configure(
        state_dir=state_dir,
        default_limit=1000,
        site_limits={"shop": 100, "news": 300},
        proxies={
            "shop": ["10.0.0.1", "10.0.0.2"],
            "news": ["10.0.0.3", "10.0.0.4"],
        },
    )

    # 1) 两个站点的混合流量，各自记账、互不挤占。
    consume("shop", "10.0.0.1", 60)
    consume("news", "10.0.0.3", 120)
    consume("shop", "10.0.0.1", 30)
    consume("news", "10.0.0.3", 80)
    print("shop 剩余 %d，news 剩余 %d（额度独立）\n"
          % (remaining("shop"), remaining("news")))

    # 2) shop 在 10.0.0.1 上再消耗 40 会超额（90+40 > 100）：
    #    不中断任务，自动切到 10.0.0.2，并返回原出口的可等待秒数。
    result = consume("shop", "10.0.0.1", 40)
    show("shop 超额触发出口切换", result)

    # 3) 继续在新出口上跑，任务不中断。
    consume("shop", "10.0.0.2", 30)
    print("切换后 shop 剩余 %d\n" % remaining("shop"))

    # 4) 日报与月报：消耗、剩余、分出口明细、切换记录。
    show("当日对账", daily_report())
    show("当月对账", monthly_report())

    # 5) 对账单落盘。
    day_path, month_path = save_reports()
    print("对账单已写入:")
    print(" - %s" % day_path)
    print(" - %s" % month_path)


if __name__ == "__main__":
    main()
