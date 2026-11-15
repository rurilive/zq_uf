"""基线驱动的质量告警引擎。

- 基线：最近窗口样本 + 同周期（季节性）样本，中位数/MAD 稳健统计
- 告警：突变（强 z 分）或缓慢漂移（连续多周期同向偏离）
- 误报抑制：值班标记后不再重复提醒，序列恢复后自动重新武装
- 回测：与生产同一份 check 逻辑、同一份数据，并与旧固定阈值对比
- 影子运行：只记录不告警，按人工标签对比新旧规则的误报率
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from . import stat


@dataclass
class Config:
    window: int = 14                 # 最近窗口长度（周期数）
    season: int | None = 7           # 同周期长度；None 表示不按周期取样本
    min_samples: int = 5             # 基线最少样本，不足走冷启动固定阈值
    deviation_z: float = 3.0         # 突变告警的稳健 z 分阈值（尺度有效时）
    spike_relative_change: float = 0.5  # 突变告警的相对幅度门槛（尺度失效/过小时据此判断）
    drift_consecutive: int = 3       # 缓慢漂移连续确认周期数
    min_relative_change: float = 0.10  # 偏离至少达到的相对幅度（方向化），过滤零尺度抖动
    cold_start_threshold: float | None = 0.05  # 样本不足时的兜底固定阈值；None 表示基线就绪前不告警
    history_snippet: int = 7         # 告警附带的最近历史片段长度
    direction: str = "both"          # down / up / both


@dataclass
class _Series:
    values: list = field(default_factory=list)
    periods: list = field(default_factory=list)
    breach_count: int = 0
    suppressed: bool = False
    suppressed_alert_id: str | None = None
    suppress_reason: str | None = None
    auto_period: int = 0


class Engine:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self._series: dict[tuple, _Series] = {}
        self._shadow_log: list = []
        self._seq = 0

    def reset(self):
        self._series.clear()
        self._shadow_log.clear()
        self._seq = 0

    def _get(self, key) -> _Series:
        if key not in self._series:
            self._series[key] = _Series()
        return self._series[key]

    def _next_id(self, key) -> str:
        self._seq += 1
        return f"{key[0]}:{key[1]}#{self._seq}"

    # ------------------------------------------------------------------ #
    # 核心：单个观测点的基线评估
    # ------------------------------------------------------------------ #
    def _baseline_samples(self, s: _Series, period):
        """最近窗口样本 + 同周期历史样本（不含当前点）。"""
        recent = s.values[-self.config.window :]
        picked = list(recent)
        if self.config.season is not None and period is not None and s.periods:
            # 与当前点同相位的历史点：按 period 标签匹配，取最近的若干个完整季节
            same_phase = [
                s.values[i]
                for i in range(len(s.values) - 1, -1, -1)
                if s.periods[i] == period
            ][: self.config.window]
            picked.extend(same_phase)
        return picked

    def _evaluate(self, dataset, metric, value, period, s: _Series):
        key = (dataset, metric)
        alert_id = self._next_id(key)
        samples = self._baseline_samples(s, period)

        base = {
            "alert_id": alert_id,
            "dataset": dataset,
            "metric": metric,
            "period": period,
            "value": value,
            "baseline": None,
            "deviation": None,
            "relative_change": None,
            "z": None,
            "samples": list(samples),
            "consecutive_breaches": 0,
            "reason": None,
        }

        # 冷启动：样本不足，沿用固定阈值兜底（配置为 None 则基线就绪前不告警）
        if len(samples) < self.config.min_samples:
            threshold = self.config.cold_start_threshold
            raw_alert = threshold is not None and value > threshold
            base.update(
                reason="cold_start_threshold" if raw_alert else None,
                raw_alert=raw_alert,
            )
            return base

        bl = stat.baseline(samples)
        dev = stat.deviation(value, bl["median"], bl["scale"], self.config.direction)
        base["baseline"] = bl
        base["deviation"] = dev["deviation"]
        base["relative_change"] = dev["relative_change"]
        base["z"] = dev["z"]

        direction_ok = (
            self.config.direction == "both"
            or (self.config.direction == "down" and value < bl["median"])
            or (self.config.direction == "up" and value > bl["median"])
        )
        meaningful = dev["relative_change"] >= self.config.min_relative_change
        breach = bool(direction_ok and meaningful)

        if breach:
            s.breach_count += 1
        else:
            s.breach_count = 0
        base["consecutive_breaches"] = s.breach_count

        if not breach:
            base["raw_alert"] = False
            return base

        # 突变（即时告警）满足任一即可：
        # 1) 尺度有效且稳健 z 分超阈值；
        # 2) 尺度失效（基线完全平稳，z=inf）但相对幅度超过尖峰门槛——
        #    大断崖不能等连续确认，小偏离则留给漂移通道。
        strong_z = math.isfinite(dev["z"]) and dev["z"] >= self.config.deviation_z
        strong_rel = (
            not math.isfinite(dev["z"])
            and dev["relative_change"] >= self.config.spike_relative_change
        )
        drifting = s.breach_count >= self.config.drift_consecutive
        if strong_z or strong_rel:
            base["reason"] = "deviation"
            base["raw_alert"] = True
        elif drifting:
            base["reason"] = "consecutive_drift"
            base["raw_alert"] = True
        else:
            base["raw_alert"] = False
        return base

    # ------------------------------------------------------------------ #
    # 对外：观测 + 告警（含误报抑制、历史片段、影子模式）
    # ------------------------------------------------------------------ #
    def check(
        self,
        dataset,
        metric="value",
        value=None,
        period=None,
        shadow=False,
    ):
        """摄入一个点并返回告警结果。

        metric 为 dict 时按旧接口解析（{"value": x, "period": p}）。
        shadow=True 只记录不告警，供影子运行与人工判断对比。
        """
        if isinstance(metric, dict):
            payload = metric
            value = float(payload.get("value", 0))
            period = payload.get("period", period)
            metric = payload.get("metric", "value")

        key = (dataset, metric)
        s = self._get(key)
        if period is None:
            period = s.auto_period
            s.auto_period += 1

        result = self._evaluate(dataset, metric, value, period, s)

        if shadow:
            # 影子运行只记录：不触碰抑制状态、不产生真实告警。
            # breach_count 仍在 _evaluate 内正常累积，使漂移确认在影子期同样可观测。
            result.setdefault("suppressed", False)
            result["alert"] = False
        else:
            # 抑制管理：偏离消失（breach 解除）视为恢复，自动重新武装；
            # 仍在偏离但未达告警条件的点保持抑制，避免标记后被下一个点提前解除
            if result["consecutive_breaches"] == 0:
                if s.suppressed:
                    s.suppressed = False
                    s.suppressed_alert_id = None
                    s.suppress_reason = None
            elif s.suppressed:
                result["reason"] = "suppressed_false_positive"
                result["suppressed"] = True
                result["false_positive_ref"] = s.suppressed_alert_id
            result.setdefault("suppressed", False)
            result["alert"] = bool(result["raw_alert"]) and not s.suppressed

        # 记录历史（评估完成后追加，当前点不进自己的基线）
        s.values.append(value)
        s.periods.append(period)
        result["history"] = list(s.values[-self.config.history_snippet :])

        if shadow:
            self._shadow_log.append(
                {
                    "alert_id": result["alert_id"],
                    "dataset": dataset,
                    "metric": metric,
                    "period": period,
                    "value": value,
                    "new_alert": bool(result["raw_alert"]),
                    "label": None,
                    "reason": result["reason"],
                    "z": result["z"],
                    "relative_change": result["relative_change"],
                }
            )
            # 影子运行不产生真实告警（已在上方置 False）
            result["shadow"] = True
        else:
            result["shadow"] = False
        return result

    # ------------------------------------------------------------------ #
    # 趋势：按 dataset × metric 独立统计
    # ------------------------------------------------------------------ #
    def trend(self, dataset, metric="value", window=None):
        key = (dataset, metric)
        s = self._series.get(key)
        if not s or not s.values:
            return {
                "dataset": dataset,
                "metric": metric,
                "history": [],
                "direction": None,
                "slope": 0.0,
                "relative_slope": 0.0,
                "change": 0.0,
                "relative_change": 0.0,
            }
        values = list(s.values) if window is None else list(s.values[-window:])
        slope, intercept = stat.linear_trend(values)
        first, last = values[0], values[-1]
        total_change = last - first
        rel_slope = slope / max(abs(stat.median(values)), stat.REL_FLOOR)
        rel_change = total_change / max(abs(first), stat.REL_FLOOR) if len(values) > 1 else 0.0
        if len(values) < 2:
            direction = None
        elif abs(rel_change) < self.config.min_relative_change:
            direction = "flat"
        else:
            direction = "up" if total_change > 0 else "down"
        return {
            "dataset": dataset,
            "metric": metric,
            "history": list(s.values),
            "direction": direction,
            "slope": slope,
            "relative_slope": rel_slope,
            "intercept": intercept,
            "change": total_change,
            "relative_change": rel_change,
        }

    # ------------------------------------------------------------------ #
    # 误报标记：标记后不再重复提醒，恢复后重新武装
    # ------------------------------------------------------------------ #
    def mark_false_positive(self, dataset, metric="value", alert_id=None, note=None):
        key = (dataset, metric)
        s = self._series.get(key)
        if s is None:
            raise KeyError(f"no series for {key}")
        if alert_id is None and s.values:
            alert_id = self._next_id(key)
        s.suppressed = True
        s.suppressed_alert_id = alert_id
        s.suppress_reason = note or "marked_false_positive"
        return {
            "dataset": dataset,
            "metric": metric,
            "suppressed": True,
            "alert_id": alert_id,
            "reason": s.suppress_reason,
        }

    # ------------------------------------------------------------------ #
    # 旧固定阈值：用于影子对比与回测的基线参照
    # ------------------------------------------------------------------ #
    def _legacy_alert(self, value) -> bool:
        threshold = self.config.cold_start_threshold
        if threshold is None:
            threshold = 0.05  # 旧系统的固定阈值
        return value > threshold

    # ------------------------------------------------------------------ #
    # 影子运行：人工标签对比
    # ------------------------------------------------------------------ #
    def label_shadow(self, alert_id, label):
        """label: 1=真实问题，0=误报。"""
        for rec in self._shadow_log:
            if rec["alert_id"] == alert_id:
                rec["label"] = int(label)
                return rec
        raise KeyError(f"unknown shadow alert_id: {alert_id}")

    def shadow_report(self):
        log = self._shadow_log
        labeled = [r for r in log if r["label"] is not None]
        new_alerted = [r for r in labeled if r["new_alert"]]
        legacy_alerted = [
            r for r in labeled if self._legacy_alert(r["value"])
        ]

        def _split(rows):
            tp = sum(1 for r in rows if r["label"] == 1)
            fp = len(rows) - tp
            return tp, fp

        n_tp, n_fp = _split(new_alerted)
        l_tp, l_fp = _split(legacy_alerted)
        total_real = sum(1 for r in labeled if r["label"] == 1)

        def _rate(fp, tp):
            return fp / (fp + tp) if fp + tp else 0.0

        mismatches = [
            {"alert_id": r["alert_id"], "value": r["value"], "label": r["label"],
             "new_alert": r["new_alert"],
             "legacy_alert": self._legacy_alert(r["value"])}
            for r in labeled
            if r["new_alert"] != bool(r["label"])
        ]
        return {
            "observations": len(log),
            "labeled": len(labeled),
            "real_incidents": total_real,
            "new_rule": {
                "alerts": len(new_alerted),
                "true_positives": n_tp,
                "false_positives": n_fp,
                "false_positive_rate": _rate(n_fp, n_tp),
                "recall": (n_tp / total_real) if total_real else 0.0,
            },
            "legacy_threshold": {
                "alerts": len(legacy_alerted),
                "true_positives": l_tp,
                "false_positives": l_fp,
                "false_positive_rate": _rate(l_fp, l_tp),
                "recall": (l_tp / total_real) if total_real else 0.0,
            },
            "false_positive_reduction": (
                (l_fp - n_fp) / l_fp if l_fp else 0.0
            ),
            "mismatches": mismatches[:20],
        }

    # ------------------------------------------------------------------ #
    # 回测：同一份数据跑新基线与旧固定阈值，可扫阈值
    # ------------------------------------------------------------------ #
    def backtest(self, points):
        """points: [{dataset, metric?, value, period?, label?}, ...]

        label=1 表示该点是真实异常，0 表示正常；无 label 的点只统计告警量。
        回测开启全新引擎实例，不污染生产状态，且不启用误报抑制。
        """
        new_engine = Engine(self.config)
        new_alerts, legacy_alerts = [], []
        for p in points:
            r = new_engine.check(
                p["dataset"],
                p.get("metric", "value"),
                p["value"],
                period=p.get("period"),
            )
            r["label"] = p.get("label")
            new_alerts.append(r)
            legacy_alerts.append(
                {
                    "dataset": p["dataset"],
                    "metric": p.get("metric", "value"),
                    "value": p["value"],
                    "period": p.get("period"),
                    "label": p.get("label"),
                    "alert": self._legacy_alert(p["value"]),
                }
            )
        return {
            "config": _config_dict(self.config),
            "points": len(points),
            "new_rule": _summarize(new_alerts),
            "legacy_threshold": _summarize(legacy_alerts),
            "alerts": new_alerts,
        }

    def sweep_thresholds(self, points, deviation_zs=(2.0, 2.5, 3.0, 3.5, 4.0),
                         drift_consecutives=(2, 3, 4)):
        """阈值网格回测：同一份数据上评估不同参数组合。"""
        results = []
        for z in deviation_zs:
            for k in drift_consecutives:
                cfg = Config(**{**_config_dict(self.config),
                                "deviation_z": z, "drift_consecutive": k})
                r = Engine(cfg).backtest(points)
                results.append({
                    "deviation_z": z,
                    "drift_consecutive": k,
                    "alerts": r["new_rule"]["alerts"],
                    "false_positives": r["new_rule"]["false_positives"],
                    "true_positives": r["new_rule"]["true_positives"],
                    "false_negative": r["new_rule"]["false_negative"],
                    "false_positive_rate": r["new_rule"]["false_positive_rate"],
                    "recall": r["new_rule"]["recall"],
                })
        return results


def _config_dict(cfg: Config) -> dict:
    return {f.name: getattr(cfg, f.name) for f in cfg.__dataclass_fields__.values()}


def _confusion(rows, alert_key):
    tp = fp = fn = tn = 0
    for r in rows:
        if r.get("label") is None:
            continue
        alerted = r[alert_key]
        if alerted and r["label"] == 1:
            tp += 1
        elif alerted and r["label"] == 0:
            fp += 1
        elif not alerted and r["label"] == 1:
            fn += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def _summarize(rows):
    total = len(rows)
    alerts = sum(1 for r in rows if r["alert"])
    tp, fp, fn, tn = _confusion(rows, "alert")
    labeled = tp + fp + fn + tn
    return {
        "alerts": alerts,
        "alert_rate": alerts / total if total else 0.0,
        "true_positives": tp,
        "false_positives": fp,
        "false_negative": fn,
        "true_negative": tn,
        "false_positive_rate": fp / (fp + tp) if fp + tp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "labeled_points": labeled,
    }
