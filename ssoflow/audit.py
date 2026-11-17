"""审计存储：追加写的登录尝试、异常项与跨应用链路。

进程内存储（与原实现一致），但不再只保留每个账号最后一条，而是完整追加，
从而支持按账号回看、按应用统计、按天汇总以及时间区间筛选。
"""

from .classify import R_MFA_BYPASSED, R_MFA_FAILED

# 合成时间的固定基准（2026-01-01T00:00:00Z），每次取号 +1 秒，结果稳定可复现。
_SYNTH_BASE = 1767225600.0


class AuditStore:
    def __init__(self):
        self.reset()

    def reset(self):
        self.attempts = []       # 每次 login 一条完整尝试
        self.abnormal = []       # 失败事件 + 多因素绕过事件，逐条展开
        self.chains = []         # 每次 login 的跨应用链路
        self._seq_by_user = {}   # 每账号尝试序号（生成 attempt_id）
        self._last_fail = {}     # 每账号上一次失败的 epoch（算连续失败间隔）
        self._tick = 0

    def next_synth_time(self):
        """取下一个合成时间戳（epoch 秒）。"""
        self._tick += 1
        return _SYNTH_BASE + self._tick

    def record(self, attempt):
        """追加一次登录尝试及其异常项、链路，并回填连续失败间隔。"""
        user = attempt["user"]
        seq = self._seq_by_user.get(user, 0) + 1
        self._seq_by_user[user] = seq
        attempt["attempt_id"] = "%s#%d" % (user, seq)

        if not attempt["ok"]:
            ts = attempt["time"]
            last = self._last_fail.get(user)
            attempt["failure_interval"] = None if last is None else round(ts - last, 3)
            self._last_fail[user] = ts
        else:
            attempt["failure_interval"] = None

        self.attempts.append(attempt)
        self.abnormal.extend(attempt["abnormal"])
        self.chains.append(attempt["chain"])
        return attempt

    def report(self, user=None, app=None, since=None, until=None):
        """汇总。user/app/since/until 只过滤 abnormal 与 chains，聚合数字保持全局。"""
        attempts = self.attempts
        failed_attempts = [a for a in attempts if not a["ok"]]

        reasons = {}                 # 环节 -> 原因 -> 次数
        mfa = {"failed": 0, "bypassed": 0}
        failures_by_app = {}         # 账号 -> 应用 -> 失败次数
        failures_by_day = {}         # 日期 -> 失败次数

        for attempt in attempts:
            bucket = attempt["failures_by_app"]
            if bucket:
                failures_by_app.setdefault(attempt["user"], {})
                for application, count in bucket.items():
                    failures_by_app[attempt["user"]][application] = (
                        failures_by_app[attempt["user"]].get(application, 0) + count
                    )
            for day, count in attempt["failures_by_day"].items():
                failures_by_day[day] = failures_by_day.get(day, 0) + count
            for item in attempt["abnormal"]:
                stage = item["stage"]
                reason = item["reason"]
                reasons.setdefault(stage, {})
                reasons[stage][reason] = reasons[stage].get(reason, 0) + 1
                if reason == R_MFA_FAILED:
                    mfa["failed"] += 1
                elif reason == R_MFA_BYPASSED:
                    mfa["bypassed"] += 1

        def in_window(ts):
            if since is not None and ts < since:
                return False
            if until is not None and ts > until:
                return False
            return True

        filtered_abnormal = [
            item
            for item in self.abnormal
            if (user is None or item["user"] == user)
            and (app is None or item.get("app") == app)
            and in_window(item["time"])
        ]

        filtered_chains = []
        for attempt in attempts:
            if user is not None and attempt["user"] != user:
                continue
            chain = attempt["chain"]
            if app is not None and not any(hop["app"] == app for hop in chain):
                continue
            start = chain[0]["time"] if chain else attempt["time"]
            if not in_window(start):
                continue
            filtered_chains.append(chain)

        return {
            "users": len({a["user"] for a in attempts}),
            "attempts": len(attempts),
            "failed": len(failed_attempts),
            "reasons": reasons,
            "mfa": mfa,
            "failures_by_app": failures_by_app,
            "failures_by_day": failures_by_day,
            "chains": filtered_chains,
            "abnormal": filtered_abnormal,
        }
