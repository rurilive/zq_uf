"""统一入口 call(fn, policy)：分类重试、抖动退避、计数上报。"""
import random
import time
from collections import defaultdict

from .errors import ErrorClass, classify
from .metrics import metrics as default_metrics
from .policy import Policy


def compute_delay(policy, error_class, retry_index, exc=None):
    """计算第 ``retry_index`` 次（从 0 开始）重试前的退避秒数。

    指数退避后按错误类别上限截断，再施加 ±jitter 比例抖动并二次截断，
    保证结果始终落在 ``[0, max_delay]`` 内；异常携带 ``retry_after`` 时
    作为等待下限参与计算。
    """
    config = policy.class_config(error_class)
    max_delay = config["max_delay"]
    delay = policy.base_delay * (policy.factor ** retry_index)
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        delay = max(delay, float(retry_after))
    delay = min(delay, max_delay)
    if policy.jitter:
        delay *= 1.0 + policy.jitter * random.uniform(-1.0, 1.0)
    return max(0.0, min(delay, max_delay))


def call(fn, policy=None):
    """按策略调用 ``fn``，对可重试错误分类退避，永久错误立即抛出。

    ``policy`` 支持字典或 :class:`~retryall.policy.Policy`；站点覆盖通过
    字典中的 ``site`` 键激活。重试次数与耗时写入统一计数器。
    """
    resolved = Policy.wrap(policy)
    if resolved.site:
        resolved = resolved.for_site(resolved.site)
    registry = resolved.metrics or default_metrics
    site = resolved.site or "default"

    started = time.monotonic()
    registry.record_call(site)
    total_attempts = 0
    class_attempts = defaultdict(int)
    try:
        while True:
            total_attempts += 1
            try:
                value = fn()
            except Exception as exc:
                error_class = classify(exc)
                registry.record_attempt(site, error_class)
                if error_class == ErrorClass.PERMANENT:
                    raise
                class_attempts[error_class] += 1
                config = resolved.class_config(error_class)
                if resolved.attempts is not None and total_attempts >= resolved.attempts:
                    raise
                if class_attempts[error_class] >= config["attempts"]:
                    raise
                delay = compute_delay(
                    resolved, error_class, class_attempts[error_class] - 1, exc
                )
                registry.record_retry(site, error_class, delay)
                time.sleep(delay)
                continue
            registry.record_success(site)
            return value
    except Exception:
        registry.record_failure(site)
        raise
    finally:
        registry.record_elapsed(site, time.monotonic() - started)
