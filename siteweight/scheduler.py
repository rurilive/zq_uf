"""动态权重调度器：按成功率与更新频率调权，低权站点有保底配额。"""

MIN_SAMPLES = 3
MIN_WEIGHT = 0.1
MAX_WEIGHT = 3.0
SUCCESS_RATIO = 0.7
UPDATE_RATIO = 0.3
DECAY = 0.5
GUARANTEE_WINDOW = 10


class Scheduler:
    """按历史反馈动态调整站点权重，接口为 next() 与 feedback(site, result)。

    权重公式（attempts >= min_samples 时生效，否则取默认权重 1.0）：
        score  = success_ratio * 成功率 + update_ratio * 更新率   （归一化后 ∈ [0, 1]）
        penalty = decay ** 连续失败次数
        weight = min_weight + (max_weight - min_weight) * score * penalty
    成功一次即清零连续失败计数，penalty 回到 1，权重回升。
    保底：任意站点连续 guarantee_window 次未被选中时强制选中。
    """

    def __init__(self, sites, *, min_samples=MIN_SAMPLES, min_weight=MIN_WEIGHT,
                 max_weight=MAX_WEIGHT, success_ratio=SUCCESS_RATIO,
                 update_ratio=UPDATE_RATIO, decay=DECAY,
                 guarantee_window=GUARANTEE_WINDOW):
        sites = list(sites)
        if not sites:
            raise ValueError("站点列表不能为空")
        if len(set(sites)) != len(sites):
            raise ValueError("站点列表存在重复: %r" % (sites,))
        if min_samples < 1:
            raise ValueError("min_samples 必须 >= 1，当前为 %r" % (min_samples,))
        if not 0 < min_weight <= max_weight:
            raise ValueError(
                "要求 0 < min_weight <= max_weight，当前为 %r/%r"
                % (min_weight, max_weight))
        if success_ratio < 0 or update_ratio < 0 or not success_ratio + update_ratio:
            raise ValueError(
                "success_ratio/update_ratio 必须非负且不同时为 0，当前为 %r/%r"
                % (success_ratio, update_ratio))
        if not 0 < decay <= 1:
            raise ValueError("decay 必须在 (0, 1] 区间，当前为 %r" % (decay,))
        if guarantee_window < len(sites):
            raise ValueError(
                "guarantee_window 必须不小于站点数 %d，当前为 %r"
                % (len(sites), guarantee_window))
        self.sites = sites
        self.min_samples = min_samples
        self.min_weight = float(min_weight)
        self.max_weight = float(max_weight)
        total = float(success_ratio + update_ratio)
        self._success_ratio = success_ratio / total
        self._update_ratio = update_ratio / total
        self.decay = float(decay)
        self.guarantee_window = guarantee_window
        self._tick = 0
        self._current = {site: 0.0 for site in sites}
        self._state = {site: self._new_state() for site in sites}
        self.weight_log = []

    @staticmethod
    def _new_state():
        return {"attempts": 0, "successes": 0, "updates": 0,
                "consecutive_failures": 0, "weight": 1.0,
                "picks": 0, "last_pick": None, "waits": []}

    def _weight_of(self, st):
        if st["attempts"] < self.min_samples:
            return 1.0
        score = (self._success_ratio * st["successes"] / st["attempts"]
                 + self._update_ratio * st["updates"] / st["attempts"])
        raw = score * self.decay ** st["consecutive_failures"]
        return self.min_weight + (self.max_weight - self.min_weight) * raw

    def next(self):
        """平滑加权轮转选站；有站点超过保底窗口未被选中时优先兜底。"""
        self._tick += 1
        site = self._overdue_site()
        if site is None:
            site = self._smooth_pick()
        st = self._state[site]
        if st["last_pick"] is not None:
            st["waits"].append(self._tick - st["last_pick"])
        st["last_pick"] = self._tick
        st["picks"] += 1
        return site

    def _overdue_site(self):
        overdue = [s for s in self.sites
                   if self._tick - (self._state[s]["last_pick"] or 0)
                   >= self.guarantee_window]
        if not overdue:
            return None
        return max(overdue,
                   key=lambda s: self._tick - (self._state[s]["last_pick"] or 0))

    def _smooth_pick(self):
        total = sum(self._state[s]["weight"] for s in self.sites)
        best = None
        for site in self.sites:
            self._current[site] += self._state[site]["weight"]
            if best is None or self._current[site] > self._current[best]:
                best = site
        self._current[best] -= total
        return best

    def feedback(self, site, result):
        """记录一次采集结果并重新计算权重，返回新权重。

        result 为 bool，或 {"ok": bool, "updated": bool} 字典。
        未知站点、非法结果类型一律抛异常，不静默跳过。
        """
        if site not in self._state:
            raise KeyError("未知站点: %r" % (site,))
        if isinstance(result, dict):
            if "ok" not in result:
                raise ValueError("result 字典缺少 'ok' 字段: %r" % (result,))
            ok = bool(result["ok"])
            updated = bool(result.get("updated", False))
        elif isinstance(result, bool):
            ok, updated = result, False
        else:
            raise TypeError(
                "result 必须是 bool 或 {'ok': ..., 'updated': ...}，当前为 %r"
                % (result,))
        st = self._state[site]
        st["attempts"] += 1
        if ok:
            st["successes"] += 1
            st["consecutive_failures"] = 0
        else:
            st["consecutive_failures"] += 1
        if updated:
            st["updates"] += 1
        new_weight = self._weight_of(st)
        if abs(new_weight - st["weight"]) > 1e-9:
            self.weight_log.append({"tick": self._tick, "site": site,
                                    "old": st["weight"], "new": new_weight})
            st["weight"] = new_weight
        return new_weight

    def stats(self, site=None):
        """返回站点统计；site 为 None 时返回全部站点的字典。"""
        if site is None:
            return {s: self.stats(s) for s in self.sites}
        if site not in self._state:
            raise KeyError("未知站点: %r" % (site,))
        st = self._state[site]
        waits = st["waits"]
        attempts = st["attempts"]
        return {"weight": st["weight"],
                "attempts": attempts,
                "successes": st["successes"],
                "updates": st["updates"],
                "success_rate": st["successes"] / attempts if attempts else None,
                "update_rate": st["updates"] / attempts if attempts else None,
                "consecutive_failures": st["consecutive_failures"],
                "picks": st["picks"],
                "avg_wait": sum(waits) / len(waits) if waits else None,
                "max_wait": max(waits) if waits else None}
