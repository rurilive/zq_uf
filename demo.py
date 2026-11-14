"""demo：模拟一个慢页面，演示组合条件等待与超时现场。

慢页面时间线（从打开页面算起）：
  0.15s  .card 渲染出来
  0.35s  出现文本 "数据加载完成"
  0.60s  网络进入空闲
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jsrender import all_of, any_of, network_idle, selector, text, wait


class SlowPage:
    def __init__(self):
        self._opened = time.monotonic()
        self._timeline = {
            "selectors": {".card": 0.15},
            "texts": {"数据加载完成": 0.35},
            "network_idle": 0.60,
        }

    def _age(self):
        return time.monotonic() - self._opened

    def exists(self, css):
        return self._age() >= self._timeline["selectors"].get(css, float("inf"))

    def text_contains(self, substr):
        return self._age() >= self._timeline["texts"].get(substr, float("inf"))

    def network_idle(self):
        return self._age() >= self._timeline["network_idle"]

    def snapshot(self):
        age = self._age()
        return {
            "selectors": {
                css: age >= at for css, at in self._timeline["selectors"].items()
            },
            "text": [
                content
                for content, at in self._timeline["texts"].items()
                if age >= at
            ],
            "network_idle": age >= self._timeline["network_idle"],
        }


def show(title, result):
    print("=" * 60)
    print(title)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


# 场景一：或组合，任一条件命中即返回，不浪费预算
page = SlowPage()
show(
    "场景一 any_of(.card 或 文本)：.card 0.15s 就绪后立即返回",
    wait(
        page,
        any_of(selector(".card"), text("数据加载完成")),
        timeout=1.0,
        interval=0.05,
    ),
)

# 场景二：与组合要求三个条件全满足，但预算只给 0.5s（网络空闲要 0.6s），超时留现场
page = SlowPage()
show(
    "场景二 all_of(.card、文本、网络空闲)：0.5s 预算不够，超时返回最后状态与快照差异",
    wait(
        page,
        all_of(selector(".card"), text("数据加载完成"), network_idle()),
        timeout=0.5,
        interval=0.05,
    ),
)
