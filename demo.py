"""演示可过期共享缓存：命中、过期后台刷新、刷新失败沿用旧规则并告警。"""

import logging
import os
import tempfile
import time

from robotscache import allowed, configure, join_refreshes, set_fetcher

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)

cache_path = os.path.join(tempfile.mkdtemp(), "robots-cache.json")
configure(cache_path=cache_path, ttl=0.3, stale_ttl=60.0, lock_timeout=5.0)

state = {"rules": {"disallow": ["/private"]}, "fail_next": False, "calls": 0}


def fetcher(host):
    print("拉取 robots:", host)
    state["calls"] += 1
    if state["fail_next"]:
        state["fail_next"] = False
        raise ConnectionError("站点 503")
    return state["rules"]


def check(label, actual, expected):
    print("%-22s => %s" % (label, actual))
    assert actual == expected, "%s: 期望 %r 实际 %r" % (label, expected, actual)


set_fetcher(fetcher)

print("== 第一段：首次拉取后命中缓存 ==")
check("allowed(/item/1) 首次", allowed("http://shop/item/1"), True)
check("allowed(/private/1)", allowed("http://shop/private/1"), False)
check("allowed(/item/1) 再次", allowed("http://shop/item/1"), True)
check("拉取次数", state["calls"], 1)

print("== 第二段：规则变更，TTL 过期后后台刷新 ==")
state["rules"] = {"disallow": ["/new-block"]}
time.sleep(0.4)
check("过期瞬间沿用旧规则", allowed("http://shop/private/1"), False)
join_refreshes()
check("后台刷新后新规则生效", allowed("http://shop/new-block/x"), False)
check("旧规则已失效", allowed("http://shop/private/1"), True)
check("累计拉取次数", state["calls"], 2)

print("== 第三段：刷新失败沿用旧规则并记录告警 ==")
state["fail_next"] = True
time.sleep(0.4)
check("刷新失败沿用旧规则", allowed("http://shop/new-block/x"), False)
join_refreshes()
check("拉取次数不变(仅失败尝试)", state["calls"], 3)

print("== 共享缓存文件 ==")
print(cache_path)
print(open(cache_path, encoding="utf-8").read())
print("全部自检通过")
