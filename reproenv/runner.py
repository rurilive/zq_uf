"""验证步骤执行器。

默认 runner 在本地按报告描述判定每一步成败，顺序如下：

1. 步骤带 ``run`` 可调用对象 -> 调用它（签名 ``(step, env)`` 或 ``(step,)``），
   返回值即 ``actual``；抛异常记为该步失败，抛 :class:`StepError` 时可带
   ``actual``/``detail``；
2. 步骤带 ``actual``（或 ``run`` 给了返回值）且有 ``expect`` -> 两者相等才
   成功，字符串忽略首尾空白，数字与其字符串写法视为相等；
3. 步骤显式给了 ``ok=False`` / ``should_fail=True`` -> 失败；
4. 其余情况（报告只写了期望）模拟执行成功，``actual`` 取 ``expect``。

自定义 ``runner=callable(step, env)`` 可完全接管：返回 ``(ok, actual, detail,
duration)`` 元组或结果字典（``duration`` 省略时回退到步骤声明的
``duration``，用于模拟超时护栏；``abort=True`` 表示资源超预期提前终止）。
"""

from ._call import invoke
from .errors import StepError


def _matches(actual, expect):
    if isinstance(actual, str) and isinstance(expect, str):
        return actual.strip() == expect.strip()
    if type(actual) is not type(expect):
        # 报告里常见 "200" 与 200 混写，只在标量可无损转换时才宽容。
        if isinstance(actual, str) or isinstance(expect, str):
            return str(actual).strip() == str(expect).strip()
    return actual == expect


def default_runner(step, env):
    """返回 ``(ok, actual, detail, duration)``；duration 为 None 表示未测量。"""
    run = step.get("run")
    if callable(run):
        try:
            value = invoke(run, (step, env))
        except StepError as exc:
            return {
                "ok": False,
                "actual": exc.actual,
                "detail": exc.detail,
                "abort": exc.abort,
            }
        except Exception as exc:  # noqa: BLE001 - 步骤脚本任何异常都算复现失败
            return False, None, "%s: %s" % (type(exc).__name__, exc), None
        actual = step.get("actual", value)
        has_actual = True
    else:
        actual = step.get("actual")
        has_actual = "actual" in step

    if "expect" in step and has_actual:
        ok = _matches(actual, step["expect"])
        detail = "" if ok else "期望 %r，实际 %r" % (step["expect"], actual)
        return ok, actual, detail, None

    if step.get("ok") is False or step.get("should_fail"):
        return False, actual, step.get("detail", "步骤显式标记为失败"), None

    if not has_actual:
        actual = step.get("expect")
    return True, actual, "", None
