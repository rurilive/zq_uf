"""验证步骤执行器。

默认 runner 在本地模拟执行报告里的步骤，按以下顺序判定成败：

1. 步骤带 ``run`` 可调用对象 -> 调用它，返回值可作为 actual，抛异常即失败；
2. 步骤带 ``actual`` 且有 ``expect`` -> 两者相等才成功（字符串忽略首尾空白）；
3. 步骤显式给了 ``ok=False`` / ``should_fail=True`` -> 失败；
4. 其余情况模拟执行成功，actual 取 ``expect``（报告只描述期望时）。

自定义 ``runner=callable(step, env)`` 可完全接管：返回 ``(ok, actual,
detail, duration)`` 元组或结果字典（``duration`` 给 None/省略时回退到
步骤声明的 ``duration``），抛 ``StepError`` 会被统一记成步骤失败。
"""

from .errors import StepError


def _expect_matches(actual, expect):
    if isinstance(actual, str) and isinstance(expect, str):
        return actual.strip() == expect.strip()
    return actual == expect


def default_runner(step, env):
    """返回 (ok, actual, detail, duration)；duration 为 None 表示未测量，
    由 core 回退到步骤声明的 ``duration``（用于模拟超时护栏）。"""
    run = step.get("run")
    if callable(run):
        try:
            value = run(step, env)
        except StepError as exc:
            return False, exc.actual, exc.detail, None
        except Exception as exc:  # noqa: BLE001 - 步骤脚本的任何异常都是复现失败
            return False, None, "%s: %s" % (type(exc).__name__, exc), None
        actual = step.get("actual", value)
    else:
        actual = step.get("actual")

    if "expect" in step and actual is not None:
        ok = _expect_matches(actual, step["expect"])
        detail = "" if ok else "期望 %r，实际 %r" % (step["expect"], actual)
        return ok, actual, detail, None

    if step.get("ok") is False or step.get("should_fail"):
        return False, actual, step.get("detail", "步骤显式标记为失败"), None

    if actual is None:
        actual = step.get("expect")
    return True, actual, "", None
