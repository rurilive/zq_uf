"""重构前后重试行为对比：旧逻辑固定三次 vs 新逻辑分类重试。"""
import json
import time

from retryall import (
    ErrorClass,
    HttpError,
    Metrics,
    Policy,
    call,
    compute_delay,
)


def legacy_call(fn, attempts=3):
    """重构前的实现：任何错误都固定重试、固定间隔，原样保留作对照。"""
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            time.sleep(0.01)
    raise last


def title(text):
    print("\n=== %s ===" % text)


# 1. 永久错误（HTTP 404）：旧逻辑浪费 3 次配额，新逻辑一次即停
title("404 永久错误：旧逻辑 vs 新逻辑")
legacy_calls = {"n": 0}

def not_found():
    legacy_calls["n"] += 1
    raise HttpError(404)

try:
    legacy_call(not_found)
except HttpError:
    print("旧逻辑实际调用次数: %d（每次都无意义重试）" % legacy_calls["n"])

new_calls = {"n": 0}

def not_found_new():
    new_calls["n"] += 1
    raise HttpError(404)

try:
    call(not_found_new, {"base_delay": 0})
except HttpError:
    print("新逻辑实际调用次数: %d（永久错误不重试）" % new_calls["n"])

# 2. 瞬时错误：新逻辑按错误类别走不同退避上限，带抖动
title("指数退避 + 抖动 + 分类上限（前 4 次重试，秒）")
policy = Policy({"base_delay": 0.1, "jitter": 0.25})
for error_class in (ErrorClass.TIMEOUT, ErrorClass.RATE_LIMIT):
    delays = [
        round(compute_delay(policy, error_class, index), 4)
        for index in range(4)
    ]
    cap = policy.class_config(error_class)["max_delay"]
    print("%-11s 上限 %5ss: %s" % (error_class, cap, delays))

# 3. 瞬时失败后成功：重试次数与耗时进入统一计数器
title("统一计数器上报（瞬时失败两次后成功）")
registry = Metrics()
state = {"n": 0}


def flaky():
    state["n"] += 1
    if state["n"] < 3:
        raise TimeoutError("read timed out")
    return "ok"


result = call(flaky, {"base_delay": 0, "metrics": registry, "site": "demo-site"})
print("返回值:", result)
print(json.dumps(registry.snapshot(), indent=2, sort_keys=True))

# 4. 按站点覆盖：同一错误在特殊站点只给 1 次机会
title("站点覆盖：edge 站点超时只允许 1 次尝试")
site_calls = {"n": 0}


def slow():
    site_calls["n"] += 1
    raise TimeoutError("slow")


site_policy = {
    "base_delay": 0,
    "site": "edge",
    "sites": {
        "edge": {"classes": {ErrorClass.TIMEOUT: {"attempts": 1}}},
    },
}
try:
    call(slow, site_policy)
except TimeoutError:
    print("edge 站点实际调用次数: %d" % site_calls["n"])
