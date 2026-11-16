"""登录结果可解释：失败原因按环节归类，跨应用链路串起来，异常项可筛选。

事件（event）字段：
- app:    应用名
- step:   环节，如 password / mfa / token / account
- ok:     该环节是否通过
- reason: 失败原因（可选，缺省按环节推导，如 bad password / mfa failed）
- action: 后续动作（可选，缺省按环节推导，如 retry / re-login / locked）
- ts/time: 时间戳，epoch 秒或 ISO 8601 字符串（可选，缺省取当前时间）
- bypassed: 该环节（通常是 mfa）是否被绕过

login(user) / report() 的调用方式不变；report 的筛选参数均为可选关键字参数。
"""

import time
from datetime import datetime, timezone

# 环节 -> 缺省失败原因
_DEFAULT_REASON = {
    "password": "bad password",
    "mfa": "mfa failed",
    "token": "token expired",
    "account": "account locked",
}

# 环节 -> 缺省后续动作
_DEFAULT_ACTION = {
    "password": "retry",
    "mfa": "retry",
    "token": "re-login",
    "account": "locked",
}

# name -> {"attempts": [record, ...], "failure_times": [ts, ...]}
_USERS = {}


def reset():
    """清空账号记录。"""
    _USERS.clear()
    return {"users": 0}


def _parse_ts(value):
    """把 epoch 秒（int/float）或 ISO 8601 字符串转成 epoch 秒，无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _day_key(ts):
    """epoch 秒 -> UTC 当天日期字符串 YYYY-MM-DD。"""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _enrich_event(event, base_ts):
    """补齐单个事件的环节、原因、动作、时间戳。"""
    app = event.get("app") or "unknown"
    stage = event.get("step") or event.get("stage") or "unknown"
    ok = bool(event.get("ok"))
    bypassed = bool(event.get("bypassed") or event.get("bypass"))

    raw_ts = event.get("ts", event.get("time", base_ts))
    ts = _parse_ts(raw_ts)
    if ts is None:
        ts = time.time()

    if bypassed:
        reason = event.get("reason") or "mfa bypassed"
        action = event.get("action") or "allow"
    elif ok:
        reason = event.get("reason") or ""
        action = event.get("action") or ""
    else:
        reason = event.get("reason") or _DEFAULT_REASON.get(stage, "%s failed" % stage)
        action = event.get("action") or _DEFAULT_ACTION.get(stage, "retry")

    return {
        "app": app,
        "stage": stage,
        "ok": ok,
        "bypassed": bypassed,
        "reason": reason,
        "action": action,
        "ts": ts,
    }


def _build_chain(events):
    """按出现顺序把事件串成跨应用链路，保留跳转顺序与每跳耗时（秒）。"""
    chain = []
    for index, event in enumerate(events):
        if chain and chain[-1]["app"] == event["app"]:
            hop = chain[-1]
        else:
            elapsed = 0.0 if not chain else round(event["ts"] - chain[-1]["ts"], 6)
            hop = {
                "seq": len(chain),
                "app": event["app"],
                "from_app": chain[-1]["app"] if chain else "",
                "ts": event["ts"],
                "elapsed": elapsed,
                "ok": True,
                "steps": [],
            }
            chain.append(hop)
        hop["steps"].append({
            "seq": index,
            "stage": event["stage"],
            "ok": event["ok"],
            "reason": event["reason"],
            "action": event["action"],
        })
        if not event["ok"]:
            hop["ok"] = False
    return chain


def login(user):
    """记录一次登录的全部尝试：失败原因、所处环节、后续动作、链路、异常项。"""
    if not isinstance(user, dict) or not user.get("user"):
        raise ValueError("user is required")
    name = user["user"]

    base_ts = _parse_ts(user.get("ts", user.get("time")))
    if base_ts is None:
        base_ts = time.time()

    raw_events = user.get("events") or []
    events = [_enrich_event(event, base_ts) for event in raw_events]

    state = _USERS.setdefault(name, {"attempts": [], "failure_times": []})

    # 同一账号连续失败的间隔：与该账号上一次失败事件的时间差
    failure_times = state["failure_times"]
    for event in events:
        event["interval"] = (
            round(event["ts"] - failure_times[-1], 6) if failure_times else None
        )
        if not event["ok"]:
            failure_times.append(event["ts"])

    first_failure = next((event for event in events if not event["ok"]), None)
    abnormal = [
        {
            "user": name,
            "app": event["app"],
            "stage": event["stage"],
            "reason": event["reason"],
            "action": event["action"],
            "ts": event["ts"],
            "interval": event["interval"],
            "bypassed": event["bypassed"],
        }
        for event in events
        if not event["ok"] or event["bypassed"]
    ]

    record = {
        "user": name,
        "ok": all(event["ok"] for event in events),
        "reason": first_failure["reason"] if first_failure else "",
        "stage": first_failure["stage"] if first_failure else "",
        "action": first_failure["action"] if first_failure else "",
        "ts": events[0]["ts"] if events else base_ts,
        "events": events,
        "chain": _build_chain(events),
        "abnormal": abnormal,
    }
    state["attempts"].append(record)
    return dict(record)


def _in_window(ts, since, until):
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    return True


def report(user=None, app=None, since=None, until=None):
    """汇总所有账号的登录记录。

    可选筛选（调用方式与原来 report() 兼容）：
    - user:  只看该账号
    - app:   只看涉及该应用的链路/异常
    - since/until: 时间范围，epoch 秒或 ISO 8601 字符串
    """
    since_ts = _parse_ts(since)
    until_ts = _parse_ts(until)

    reasons = {}          # 环节 -> 原因 -> 次数（失败原因按环节归类）
    mfa = {"failed": 0, "bypassed": 0}
    failures_by_app = {}  # 账号 -> 应用 -> 失败次数
    failures_by_day = {}  # 日期 -> 失败次数
    intervals = {}        # 账号 -> 连续失败间隔（秒）
    by_user = {}
    chains = []
    abnormal = []
    total_attempts = 0
    total_failed = 0

    for name, state in _USERS.items():
        if user is not None and name != user:
            continue

        user_app_failures = {}
        user_failure_times = []
        user_attempts = 0
        user_failed_attempts = 0

        for record in state["attempts"]:
            user_attempts += 1
            if not record["ok"]:
                user_failed_attempts += 1

            chain_apps = {hop["app"] for hop in record["chain"]}
            if app is not None and app not in chain_apps:
                continue
            # 链路内任一跳转落在时间窗内即保留整条会话链路
            if not any(
                _in_window(hop["ts"], since_ts, until_ts)
                for hop in record["chain"]
            ):
                continue
            chains.append({
                "user": name,
                "ts": record["ts"],
                "chain": record["chain"],
            })

            for event in record["events"]:
                if app is not None and event["app"] != app:
                    continue
                if not _in_window(event["ts"], since_ts, until_ts):
                    continue
                if event["bypassed"]:
                    mfa["bypassed"] += 1
                if not event["ok"]:
                    bucket = reasons.setdefault(event["stage"], {})
                    bucket[event["reason"]] = bucket.get(event["reason"], 0) + 1
                    if event["stage"] == "mfa":
                        mfa["failed"] += 1
                    user_app_failures[event["app"]] = (
                        user_app_failures.get(event["app"], 0) + 1
                    )
                    day = _day_key(event["ts"])
                    failures_by_day[day] = failures_by_day.get(day, 0) + 1
                    user_failure_times.append(event["ts"])

            for item in record["abnormal"]:
                if app is not None and item["app"] != app:
                    continue
                if _in_window(item["ts"], since_ts, until_ts):
                    abnormal.append(item)

        if user_app_failures:
            failures_by_app[name] = user_app_failures
        gaps = [
            round(curr - prev, 6)
            for prev, curr in zip(user_failure_times, user_failure_times[1:])
        ]
        if gaps:
            intervals[name] = gaps
        by_user[name] = {
            "attempts": user_attempts,
            "failed": user_failed_attempts,
            "failures_by_app": user_app_failures,
            "intervals": gaps,
        }
        total_attempts += user_attempts
        total_failed += user_failed_attempts

    return {
        "users": len(by_user),
        "attempts": total_attempts,
        "failed": total_failed,
        "reasons": reasons,
        "mfa": mfa,
        "by_user": by_user,
        "failures_by_app": failures_by_app,
        "failures_by_day": failures_by_day,
        "intervals": intervals,
        "chains": chains,
        "abnormal": abnormal,
    }
