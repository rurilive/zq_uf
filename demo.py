"""动态权重调度演示：重点站（shop）与长尾站（blog）。

所有输出均来自公开接口 weights()/weight_changes()/stats()，
与 tests/test_weight.py 中的断言口径完全一致。
"""

from siteweight import (
    STARVATION_LIMIT,
    feedback,
    next,
    reset,
    stats,
    weight_changes,
    weights,
)


def show(title):
    print(f"\n=== {title} ===")
    print("weights:", weights())


def show_changes():
    for event in weight_changes():
        print(
            f"  change: {event['site']} {event['old']} -> {event['new']}"
            f" ({event['reason']}, after pick {event['pick']})"
        )


def show_stats():
    current = stats()
    print(
        f"total_picks={current['total_picks']} "
        f"starvation_picks={current['starvation_picks']} "
        f"starvation_limit={STARVATION_LIMIT}"
    )
    for site, info in current["sites"].items():
        print(
            f"  {site}: picks={info['picks']} weight={info['weight']} "
            f"max_wait={info['max_wait']} avg_wait={info['avg_wait']:.2f} "
            f"current_wait={info['current_wait']} penalized={info['penalized']}"
        )


reset()

# 阶段 1：重点站 shop 高频成功，长尾站 blog 连续失败触发降权
show("phase 1: shop busy & healthy, blog keeps failing")
for _ in range(5):
    feedback("shop", True)
for _ in range(3):
    feedback("blog", False)
feedback("news", True)
show_changes()
show("weights after feedback")

# 阶段 2：纯调度 24 轮，观察低权长尾站的保底与等待时间
print("\n=== phase 2: 24 scheduling rounds, no new feedback ===")
for _ in range(24):
    next()
show_stats()

# 阶段 3：长尾站恢复，连续成功后权重回升
print("\n=== phase 3: blog recovers with consecutive successes ===")
feedback("blog", True)
feedback("blog", True)
show_changes()
show("weights after recovery")
