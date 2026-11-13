"""可编排的多步登录流程：步骤声明、依赖排序、验证码回调、会话隔离。"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("loginflow")

_SENSITIVE = re.compile(r"pass|pwd|secret|captcha", re.IGNORECASE)


@dataclass
class Step:
    """一个登录步骤的声明。

    name:       步骤名，用于报告与日志。
    action:     callable(ctx) -> dict | None，返回的字段合并进上下文。
    expect:     成功特征，字段名 / {字段: 值或断言} / callable(ctx)；None 表示不校验。
    timeout:    单次执行超时秒数。
    retries:    失败后的额外重试次数。
    backoff:    重试间隔秒数。
    on_failure: 重试耗尽后的策略，"abort" 或 "manual"（转人工回调）。
    needs:      执行前上下文中必须具备的字段，用于乱序步骤的依赖排序。
    provides:   成功后向上下文提供的字段。
    captcha:    为 True 时先经外部验证码回调取码，写入 ctx["captcha_code"]。
    """

    name: str
    action: object
    expect: object = None
    timeout: float = 30.0
    retries: int = 0
    backoff: float = 0.0
    on_failure: str = "abort"
    needs: tuple = ()
    provides: tuple = ()
    captcha: bool = False


@dataclass
class StepResult:
    """单步执行报告：耗时、结果、重试次数与失败原因。"""

    name: str
    ok: bool
    attempts: int
    retries: int
    duration: float
    reason: str = ""


class LoginResult(dict):
    """登录上下文（dict），附带每步报告；ok 为 False 时会话不落盘。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ok = True
        self.steps = []
        self.session_file = None


def run(account, steps, callbacks=None, session_dir="sessions"):
    """按依赖顺序执行登录步骤，返回 LoginResult。

    callbacks: {"captcha": fn({"account", "step"}) -> 验证码,
                "manual":  fn(step_name, reason) -> dict | None}
    session_dir: 每账号一个会话文件，仅全部步骤成功才落盘；None 表示禁用。
    """
    callbacks = callbacks or {}
    context = LoginResult(account=account)
    for step in _order_steps(_normalize(steps)):
        result = _run_step(step, context, account, callbacks)
        context.steps.append(result)
        _log_step(result)
        if not result.ok:
            context.ok = False
            return context
    if session_dir:
        context.session_file = _save_session(session_dir, account, context)
    return context


def _normalize(steps):
    normalized = []
    for index, step in enumerate(steps, 1):
        if isinstance(step, Step):
            normalized.append(step)
        elif callable(step):
            normalized.append(Step(name="step%d" % index, action=step))
        else:
            raise TypeError("不支持的步骤类型: %r" % (step,))
    return normalized


def _order_steps(steps):
    """按 needs/provides 稳定拓扑排序，乱序声明也能正确执行。"""
    remaining = list(steps)
    available = {"account"}
    ordered = []
    while remaining:
        for step in list(remaining):
            if set(step.needs) <= available:
                ordered.append(step)
                remaining.remove(step)
                available |= set(step.provides)
                break
        else:
            names = ", ".join(s.name for s in remaining)
            raise ValueError("步骤依赖无法满足: " + names)
    return ordered


def _run_step(step, context, account, callbacks):
    started = time.monotonic()
    attempts = 0
    reason = ""
    max_attempts = 1 + max(step.retries, 0)
    while attempts < max_attempts:
        attempts += 1
        if step.captcha:
            solve = callbacks.get("captcha")
            if solve is None:
                reason = "captcha_callback_missing"
                break
            context["captcha_code"] = solve({"account": account, "step": step.name})
        try:
            update = _call_with_timeout(step.action, context, step.timeout)
        except TimeoutError:
            reason = "timeout>%.3fs" % step.timeout
        except Exception as exc:  # 只记异常类型，避免凭据进入日志
            reason = "exception:%s" % type(exc).__name__
        else:
            if update:
                context.update(update)
            reason = _check_expect(step.expect, context)
            if not reason:
                duration = time.monotonic() - started
                return StepResult(step.name, True, attempts, attempts - 1, duration)
        if attempts < max_attempts and step.backoff:
            time.sleep(step.backoff)
    duration = time.monotonic() - started
    if step.on_failure == "manual":
        return _manual_recover(step, context, callbacks, attempts, duration, reason)
    return StepResult(step.name, False, attempts, attempts - 1, duration, reason)


def _manual_recover(step, context, callbacks, attempts, duration, reason):
    manual = callbacks.get("manual")
    if manual is None:
        return StepResult(step.name, False, attempts, attempts - 1, duration,
                          "manual_required:" + reason)
    update = manual(step.name, reason)
    if isinstance(update, dict):
        context.update(update)
        return StepResult(step.name, True, attempts, attempts - 1, duration,
                          "manual_resolved")
    return StepResult(step.name, False, attempts, attempts - 1, duration,
                      "manual_aborted:" + reason)


def _call_with_timeout(action, context, timeout):
    box = {}

    def target():
        try:
            box["value"] = action(context)
        except Exception as exc:
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError("step exceeded %.3fs" % timeout)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _check_expect(expect, context):
    if expect is None:
        return ""
    if callable(expect):
        return "" if expect(context) else "expect_failed:predicate"
    if isinstance(expect, str):
        return "" if context.get(expect) else "expect_failed:" + expect
    for key, want in expect.items():
        got = context.get(key)
        ok = want(got) if callable(want) else got == want
        if not ok:
            return "expect_failed:" + key
    return ""


def _save_session(session_dir, account, context):
    os.makedirs(session_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(account))
    path = os.path.join(session_dir, safe + ".json")
    data = {k: v for k, v in context.items() if not _SENSITIVE.search(k)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def _log_step(result):
    # 只记录步骤元信息，绝不输出上下文取值，凭据不会进入日志。
    logger.info("step=%s ok=%s duration=%.3fs retries=%d reason=%s",
                result.name, result.ok, result.duration, result.retries,
                result.reason or "-")
