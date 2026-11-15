"""采集目标注册表：别名合并、自适应重访、按天指标、按站点灰度规则。"""
import hashlib
import time
from collections import defaultdict

from .rules import RuleSet, normalize, site_of

BASE_INTERVAL = 3600          # 新目标的初始重访间隔
MIN_INTERVAL = 300            # 常变页面的最短间隔
MAX_INTERVAL = 7 * 86400      # 长期不变页面的最长间隔
ALIAS_ALERT_RATIO = 0.5       # 别名/目标超过该比例告警
ALIAS_ALERT_MIN = 10          # 且别名数至少达到该值


def _day(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _fp(normalized):
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class Target:
    """同一内容目标：保留最早入队的地址为 canonical，其余记为别名。"""
    __slots__ = ("canonical", "first_seen", "last_due", "interval",
                 "aliases", "raw_urls", "fetches", "changes")

    def __init__(self, canonical, now):
        self.canonical = canonical
        self.first_seen = now
        self.last_due = now
        self.interval = BASE_INTERVAL
        self.aliases = {}              # 别名 -> 首次见到时间
        self.raw_urls = {canonical: now}  # 所有原始写法 -> 首次见到时间
        self.fetches = 0
        self.changes = 0


class Store:
    def __init__(self, rules=None):
        self.global_rules = rules or RuleSet()
        self.site_rules = {}   # site -> RuleSet，灰度中的站点规则
        self.targets = {}      # fingerprint -> Target
        self.daily = defaultdict(lambda: defaultdict(int))  # (site, day) -> 计数

    # ---------- 规则管理：灰度、推广、回退 ----------

    def rules_for(self, site):
        return self.site_rules.get(site, self.global_rules)

    def pilot_rules(self, site, ruleset):
        """新规则先在一个站点试运行，并重新判定该站历史链接。"""
        self.site_rules[site] = ruleset
        return self.recanonicalize(site)

    def rollback_rules(self, site):
        """只回退这个站点，其他采集任务不受影响。"""
        self.site_rules.pop(site, None)
        return self.recanonicalize(site)

    def promote_rules(self, site):
        """试运行确认指标下降后，把该站规则推广为全局规则。"""
        rs = self.site_rules.pop(site, None)
        if rs is None:
            return False
        self.global_rules = rs
        self.recanonicalize()
        return True

    # ---------- 核心判定 ----------

    def fingerprint(self, url):
        n = normalize(url, self.rules_for(site_of(url)))
        return _fp(n.normalized)

    def explain(self, url):
        """归一化细节：命中哪条规则、为什么这样判。"""
        n = normalize(url, self.rules_for(site_of(url)))
        return {"url": url, "normalized": n.normalized,
                "fingerprint": _fp(n.normalized),
                "rules": n.matched_rules,
                "ruleset": self.rules_for(site_of(url)).version,
                "site": site_of(url), "why": n.why}

    def due(self, url, now):
        site = site_of(url)
        rules = self.rules_for(site)
        n = normalize(url, rules)
        fp = _fp(n.normalized)
        tgt = self.targets.get(fp)
        is_alias = False
        if tgt is None:
            tgt = Target(url, now)
            self.targets[fp] = tgt
            self._bump(site, now, "new_targets")
            is_due, reason = True, "new"
            why = "新目标，首次入队"
        else:
            if url not in tgt.raw_urls:
                tgt.raw_urls[url] = now
                if url != tgt.canonical:
                    tgt.aliases[url] = now
                    is_alias = True
                    self._bump(site, now, "aliases")
            wait = now - tgt.last_due
            is_due = wait >= tgt.interval
            if is_due:
                tgt.last_due = now
            reason = "interval"
            why = "距上次到期 %ds，当前重访间隔 %ds" % (wait, tgt.interval)
        return {"url": url, "fingerprint": fp, "due": is_due,
                "interval": tgt.interval, "reason": reason, "why": why,
                "rules": n.matched_rules, "ruleset": rules.version,
                "site": site, "canonical": tgt.canonical,
                "is_alias": is_alias or (url != tgt.canonical)}

    def record_fetch(self, url, now, changed):
        """抓取回报：按更新频率调整重访间隔，常变的页面更早回来。"""
        site = site_of(url)
        n = normalize(url, self.rules_for(site))
        fp = _fp(n.normalized)
        tgt = self.targets.get(fp)
        if tgt is None:
            tgt = Target(url, now)
            self.targets[fp] = tgt
        tgt.fetches += 1
        self._bump(site, now, "fetches")
        duplicate = url != tgt.canonical
        if duplicate:
            self._bump(site, now, "duplicate_fetches")
        old = tgt.interval
        if changed:
            tgt.changes += 1
            tgt.interval = max(MIN_INTERVAL, old // 2)
            why = "内容有更新，间隔 %ds 缩短为 %ds" % (old, tgt.interval)
        else:
            tgt.interval = min(MAX_INTERVAL, int(old * 1.5))
            why = "内容未变化，间隔 %ds 放宽为 %ds" % (old, tgt.interval)
        return {"url": url, "fingerprint": fp, "changed": bool(changed),
                "interval": tgt.interval, "previous_interval": old,
                "duplicate": duplicate, "site": site, "why": why}

    # ---------- 规则变更后重新判定历史链接 ----------

    def recanonicalize(self, site=None):
        """按当前规则重新判定历史链接；同一目标只留最早入队的地址。"""
        affected = [fp for fp, t in self.targets.items()
                    if site is None or site_of(t.canonical) == site]
        groups = defaultdict(list)  # 新指纹 -> [(url, 首次见到时间, 旧Target)]
        for fp in affected:
            t = self.targets.pop(fp)
            for u, ts in t.raw_urls.items():
                n = normalize(u, self.rules_for(site_of(u)))
                groups[_fp(n.normalized)].append((u, ts, t))
        for nfp, items in groups.items():
            items.sort(key=lambda x: x[1])
            canonical, first_seen = items[0][0], items[0][1]
            tgt = Target(canonical, first_seen)
            tgt.raw_urls = {}
            tgt.last_due = max(t.last_due for _, _, t in items)
            tgt.interval = min(t.interval for _, _, t in items)
            tgt.fetches = sum(t.fetches for _, _, t in items)
            tgt.changes = sum(t.changes for _, _, t in items)
            for u, ts, _ in items:
                tgt.raw_urls[u] = ts
                if u != canonical:
                    tgt.aliases[u] = ts
            self.targets[nfp] = tgt
        return {"rejudged": len(affected), "targets": len(groups),
                "site": site}

    # ---------- 指标与告警 ----------

    def _bump(self, site, now, key):
        self.daily[(site, _day(now))][key] += 1

    def daily_metrics(self, site=None):
        """按天查看：别名比例与重复抓取次数，可按站点过滤。"""
        out = {}
        for (s, day), counts in sorted(self.daily.items()):
            if site and s != site:
                continue
            d = out.setdefault(day, defaultdict(int))
            for k, v in counts.items():
                d[k] += v
        result = {}
        for day, c in out.items():
            aliases = c.get("aliases", 0)
            targets = c.get("new_targets", 0)
            result[day] = {
                "new_targets": targets,
                "aliases": aliases,
                "alias_ratio": round(aliases / max(1, aliases + targets), 4),
                "fetches": c.get("fetches", 0),
                "duplicate_fetches": c.get("duplicate_fetches", 0),
            }
        return result

    def alias_alerts(self, ratio=ALIAS_ALERT_RATIO, min_aliases=ALIAS_ALERT_MIN):
        """别名过多的站点告警，并附可导出的样本。"""
        by_site = defaultdict(lambda: [0, 0])  # site -> [别名数, 目标数]
        for t in self.targets.values():
            s = site_of(t.canonical)
            by_site[s][0] += len(t.aliases)
            by_site[s][1] += 1
        alerts = []
        for s, (a, n) in sorted(by_site.items()):
            r = a / max(1, n)
            if a >= min_aliases and r >= ratio:
                alerts.append({"site": s, "aliases": a, "targets": n,
                               "alias_ratio": round(r, 4),
                               "samples": self.alias_samples(s)})
        return alerts

    def alias_samples(self, site, limit=5, per_target=3):
        """导出该站别名最多的若干目标样本。"""
        rows = [t for t in self.targets.values()
                if site_of(t.canonical) == site and t.aliases]
        rows.sort(key=lambda t: -len(t.aliases))
        return [{"canonical": t.canonical,
                 "alias_count": len(t.aliases),
                 "aliases": sorted(t.aliases)[:per_target]}
                for t in rows[:limit]]
