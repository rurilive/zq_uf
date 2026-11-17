"""登录流程审计：解释每次登录的结果。

login(user) 的调用方式保持不变，但返回值在原有 ok/reason/stage/chain/abnormal
基础上补充环节、失败原因代码、后续动作、跨应用链路（含顺序与耗时）、连续失败间隔等。
report() 无参即可调用，也支持按账号/应用/时间区间过滤异常登录。
"""

from .audit import AuditStore
from .classify import (
    R_MFA_BYPASSED,
    STAGE_MFA,
    action_for,
    classify_reason,
    classify_stage,
)
from .timeutil import day_of, to_epoch

_STORE = AuditStore()


def reset():
    """清空全部审计记录。"""
    _STORE.reset()
    return {"users": 0}


def _resolve_event_times(events):
    """给每个事件补 epoch：显式 ts/time 优先，否则用单调合成时间。"""
    resolved = []
    for event in events:
        raw_ts = event.get("ts", event.get("time"))
        ts = to_epoch(raw_ts)
        if ts is None:
            ts = _STORE.next_synth_time()
        resolved.append(ts)
    return resolved


def _build_chain(user, events, times):
    """按事件顺序构建跨应用链路，保留应用顺序与每次跳转相对上一应用的耗时。"""
    chain = []
    last_app = None
    last_app_time = None
    for event, ts in zip(events, times):
        app = event.get("app") or ""
        if app != last_app:
            elapsed = None if last_app_time is None else round(ts - last_app_time, 3)
            chain.append({
                "user": user,
                "seq": len(chain) + 1,
                "app": app,
                "time": ts,
                "elapsed": elapsed,
            })
            last_app = app
            last_app_time = ts
    return chain


def _detect_mfa_bypass(events, stages, ok):
    """多因素被绕过：本应有 MFA 却没有任何 MFA 事件，且最终放行。

    优先采信事件显式标记（mfa_bypassed / bypass=True），否则在 mfa_required
    且全程无 mfa 环节、登录又成功时判定为绕过。绕过单独归类为异常项。
    """
    if not ok:
        return False
    for event in events:
        if event.get("mfa_bypassed") or event.get("bypass"):
            return True
    if any(event.get("mfa_required") for event in events):
        if not any(stage == STAGE_MFA for stage in stages):
            return True
    return False


def login(user):
    """记录一次登录尝试的失败原因、所处环节、后续动作与跨应用链路。"""
    if not isinstance(user, dict) or not user.get("user"):
        raise ValueError("user is required")

    name = user["user"]
    events = user.get("events") or []
    if not isinstance(events, list):
        raise ValueError("events must be a list")

    times = _resolve_event_times(events)
    stages = [classify_stage(event.get("step")) for event in events]

    processed = []
    abnormal = []
    first_failure = None

    for index, (event, ts, stage) in enumerate(zip(events, times, stages)):
        ok = bool(event.get("ok"))
        reason = classify_reason(stage, event.get("reason"), ok)
        item = {
            "index": index,
            "app": event.get("app") or "",
            "step": event.get("step") or "",
            "stage": stage,
            "ok": ok,
            "time": ts,
            "reason": reason,
            "reason_text": "" if ok else str(event.get("reason") or reason),
            "action": "" if ok else action_for(reason),
        }
        processed.append(item)
        if not ok:
            abnormal.append({"user": name, **item})
            if first_failure is None:
                first_failure = item

    overall_ok = first_failure is None
    bypassed = _detect_mfa_bypass(events, stages, overall_ok)
    attempt_time = times[-1] if times else _STORE.next_synth_time()

    if bypassed:
        bypass_item = {
            "user": name,
            "index": len(processed),
            "app": processed[-1]["app"] if processed else "",
            "step": "mfa",
            "stage": STAGE_MFA,
            "ok": True,
            "time": attempt_time,
            "reason": R_MFA_BYPASSED,
            "reason_text": "mfa bypassed",
            "action": action_for(R_MFA_BYPASSED),
        }
        abnormal.append(bypass_item)

    chain = _build_chain(name, events, times)

    # 同一账号的失败次数按应用分别统计；失败次数按天汇总（均按失败事件计）。
    failures_by_app = {}
    failures_by_day = {}
    for item in processed:
        if item["ok"]:
            continue
        failures_by_app[item["app"]] = failures_by_app.get(item["app"], 0) + 1
        day = day_of(item["time"])
        failures_by_day[day] = failures_by_day.get(day, 0) + 1

    if first_failure is not None:
        reason = first_failure["reason"]
        stage = first_failure["stage"]
        reason_text = first_failure["reason_text"]
        action = first_failure["action"]
    elif bypassed:
        reason = R_MFA_BYPASSED
        stage = STAGE_MFA
        reason_text = "mfa bypassed"
        action = action_for(R_MFA_BYPASSED)
    else:
        reason = ""
        stage = ""
        reason_text = ""
        action = ""

    record = {
        "user": name,
        "ok": overall_ok,
        # 兼容旧字段：reason 现在是稳定的原因代码，stage 是所处环节。
        "reason": reason,
        "stage": stage,
        # 可读描述与建议的后续动作。
        "reason_text": reason_text,
        "action": action,
        "time": attempt_time,
        "mfa_bypassed": bypassed,
        "failure_interval": None,  # 由存储在追加时按上一次失败回填
        "events": processed,
        "chain": chain,
        "abnormal": abnormal,
        "failures_by_app": failures_by_app,
        "failures_by_day": failures_by_day,
    }
    _STORE.record(record)
    return dict(record)


def report(user=None, app=None, since=None, until=None):
    """汇总失败原因（按环节）、多因素异常、链路与异常项。

    无参调用返回全局汇总；传入 user/app/since/until（epoch 或 ISO 时间）
    可按账号、应用、时间区间过滤异常登录与链路，聚合数字保持全局口径。
    """
    return _STORE.report(
        user=user,
        app=app,
        since=to_epoch(since),
        until=to_epoch(until),
    )
