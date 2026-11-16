"""演示：按类型编排的处置流程、失败阻断、遗留项确认与复盘统计。"""

import json
from datetime import datetime, timedelta

from irplaybook import (
    open,
    timeline,
    run,
    confirm_leftover,
    close,
    stats,
    reset,
    set_clock,
)


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += timedelta(seconds=seconds)


clock = FakeClock(datetime(2026, 11, 17, 9, 0, 0))
reset()
set_clock(clock)


def show(title, payload):
    print("=" * 20, title, "=" * 20)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# 场景一：勒索软件，按取证先于清理的顺序正常处置
open({"id": "e1", "type": "ransomware", "operator": "li"})
clock.tick(2)
run("e1", "isolate", "li", "done", note="断网隔离受感染主机")
clock.tick(8)
run("e1", "collect", "wang", "done", note="内存镜像和磁盘快照已留存")
clock.tick(15)
run("e1", "cleanup", "wang", "done", note="清除持久化项")
clock.tick(25)
run("e1", "restore", "zhao", "done", note="从干净备份恢复")
clock.tick(10)
close("e1", "zhao")
show("勒索事件时间线（复盘材料直接取自记录）", timeline("e1"))

# 场景二：入侵事件，取证步骤超时阻断，遗留项确认后关闭
clock.tick(60)
open({"id": "e2", "type": "intrusion", "operator": "li"})
clock.tick(3)
run("e2", "isolate", "li", "done")
clock.tick(9)
run("e2", "collect", "li", "timeout", note="采集代理无响应，已手工保全日志")
show("超时后停在当前步骤，后续步骤 blocked", timeline("e2"))

clock.tick(12)
confirm_leftover("e2", "collect", "zhang", "日志手工保全，镜像补采排期明天")
# 补采成功，遗留项自动核销、后续步骤放行
clock.tick(5)
run("e2", "collect", "wang", "done", note="补采完成")
clock.tick(20)
run("e2", "cleanup", "wang", "done")
clock.tick(10)
run("e2", "review", "zhao", "done")
clock.tick(4)
run("e2", "harden", "zhao", "failed", note="加固窗口未获批")
confirm_leftover("e2", "harden", "zhang", "转入下周加固计划")
clock.tick(6)
close("e2", "zhang")
show("入侵事件关闭检查与遗留项", timeline("e2"))

# 已关闭事件按类型统计耗时
show("按类型统计处置耗时（秒）", stats())
