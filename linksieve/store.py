"""链接去重状态：内容指纹、别名归并、自适应重访与站点级试运行。

对外入口保持 ``fingerprint(url)`` 与 ``due(url, now)``；其余能力通过
``LinkStore`` 暴露。所有状态默认驻留内存，时间由调用方传入，便于回放与测试。
"""
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field

from .canonical import canonicalize, rule_hits, site_of
from .rules import DEFAULT_RULESET


BASE_INTERVAL = 3600.0
MIN_INTERVAL = 300.0
MAX_INTERVAL = 7 * 24 * 3600.0
SHRINK_FACTOR = 0.5
GROW_FACTOR = 1.5
FINGERPRINT_LENGTH = 16


@dataclass
class Target:
    """一个内容目标；首次入队的地址固定保留，其余写法记为别名。"""

    key: str
    site: str
    first_url: str
    first_seen: float
    last_seen: float
    last_fetch: float = None
    fetches: int = 0
    changes: int = 0
    interval: float = BASE_INTERVAL
    last_digest: str = None
    ruleset: str = DEFAULT_RULESET.version
    rules_hit: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)  # url -> 观测信息


@dataclass
class SiteDay:
    """单个站点单日的观测量，用于试运行期间的横向对比。"""

    urls_seen: set = field(default_factory=set)
    alias_urls: set = field(default_factory=set)
    fetched_keys: set = field(default_factory=set)
    rulesets: set = field(default_factory=set)
    new_targets: int = 0
    fetches: int = 0
    duplicate_fetches: int = 0
    unchanged_fetches: int = 0


class LinkStore:
    """可版本化规则、按站点隔离试运行的链接判重仓库。"""

    def __init__(self, rules=None, alert_min_aliases=5, alert_min_ratio=0.5):
        self.default_rules = rules or DEFAULT_RULESET
        self.alert_min_aliases = alert_min_aliases
        self.alert_min_ratio = alert_min_ratio
        self._targets = {}
        self._canary = {}
        self._pinned = {}
        self._days = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 规则选择
    # ------------------------------------------------------------------
    def rules_for(self, site):
        """站点生效规则：试运行 > 已转正 > 全局默认。"""
        return (
            self._canary.get(site)
            or self._pinned.get(site)
            or self.default_rules
        )

    def start_canary(self, site, rules=None, **overrides):
        """在单个站点启用候选规则，并按候选规则重新判定该站历史链接。

        其他站点仍使用原规则，因此回退只需调用 :meth:`rollback_site`。
        """
        if rules is None:
            base = self._pinned.get(site) or self.default_rules
            if overrides and "version" not in overrides:
                overrides["version"] = f"{base.version}+canary"
            rules = base.evolve(**overrides)
        with self._lock:
            self._canary[site] = rules
            summary = self.rejudge(site=site)
        return {"rules": rules, "rejudge": summary}

    def promote_site(self, site):
        """确认指标下降后，把候选规则固定为该站点的正式规则。"""
        with self._lock:
            rules = self._canary.pop(site, None)
            if rules is None:
                raise KeyError(f"no canary rules for site: {site}")
            self._pinned[site] = rules
        return rules

    def rollback_site(self, site, rules=None):
        """只回退该站点：移除候选/固定规则并按基线重新判定历史链接。"""
        with self._lock:
            removed = []
            if self._canary.pop(site, None) is not None:
                removed.append("canary")
            if self._pinned.pop(site, None) is not None:
                removed.append("pinned")
            summary = self.rejudge(rules=rules or self.default_rules, site=site)
        summary["rules_removed"] = removed
        summary["site"] = site
        return summary

    def set_rules(self, rules, site=None):
        """更新规则并重新判定历史链接。

        ``site`` 为空即全局规则（影响所有未单独固定的站点）；指定站点则
        只固定该站点的规则。返回重新判定的统计。
        """
        with self._lock:
            if site is None:
                self.default_rules = rules
                summary = self.rejudge(rules=rules)
            else:
                self._pinned[site] = rules
                self._canary.pop(site, None)
                summary = self.rejudge(rules=rules, site=site)
        return summary

    # ------------------------------------------------------------------
    # 指纹与判重
    # ------------------------------------------------------------------
    def fingerprint(self, url, rules=None):
        """同一内容的所有写法得到同一指纹；默认用该站点生效的规则。"""
        effective = rules or self.rules_for(site_of(url))
        key, _ = canonicalize(url, effective)
        return _hash_key(key)

    def due(self, url, now):
        """判断是否到期，返回可解释的判定结果。

        判定只看内容指纹与目标的重访间隔；内容是否变化由调用方在抓取后
        通过 :meth:`observe` 报告，进而调整该目标的间隔。
        """
        raw = (url or "").strip()
        with self._lock:
            site = site_of(raw)
            effective = self.rules_for(site)
            canonical, detail = canonicalize(raw, effective)
            key = _hash_key(canonical)
            hits = rule_hits(detail)

            target = self._targets.get(key)
            is_new = target is None
            if is_new:
                target = Target(
                    key=key,
                    site=site,
                    first_url=raw,
                    first_seen=now,
                    last_seen=now,
                    ruleset=effective.version,
                    rules_hit=hits,
                )
                self._targets[key] = target
                day = self._day(site, now)
                day.new_targets += 1
            else:
                target.last_seen = now
                target.rules_hit = hits
                target.ruleset = effective.version

            is_alias = not is_new and raw != target.first_url
            if is_alias:
                alias = target.aliases.get(raw)
                if alias is None:
                    target.aliases[raw] = {
                        "first_seen": now,
                        "last_seen": now,
                        "seen_count": 1,
                        "rules_hit": hits,
                    }
                else:
                    alias["last_seen"] = now
                    alias["seen_count"] += 1
                    alias["rules_hit"] = hits

            day = self._day(site, now)
            day.urls_seen.add(raw)
            day.rulesets.add(effective.version)
            if is_alias:
                day.alias_urls.add(raw)

            if is_new:
                due_now, reason = True, "new"
                explanation = "no earlier entry for this content fingerprint"
            else:
                waited = now - (target.last_fetch if target.last_fetch is not None
                                else target.first_seen)
                due_now = waited >= target.interval
                if due_now:
                    reason = "interval_elapsed"
                    explanation = (
                        f"{waited:.0f}s since last check, interval {target.interval:.0f}s"
                    )
                else:
                    reason = "interval_not_elapsed"
                    explanation = (
                        f"{waited:.0f}s since last check, "
                        f"{target.interval - waited:.0f}s remaining "
                        f"of {target.interval:.0f}s interval"
                    )

            if due_now:
                self._record_fetch(target, day, now)

            next_due = None
            if target.last_fetch is not None:
                next_due = target.last_fetch + target.interval

            return {
                "url": raw,
                "kept_url": target.first_url,
                "fingerprint": key,
                "canonical": canonical,
                "site": site,
                "due": due_now,
                "interval": target.interval,
                "reason": reason,
                "rules_hit": hits,
                "ruleset": effective.version,
                "is_alias": is_alias,
                "alias_of": target.first_url if is_alias else None,
                "next_due_at": next_due,
                "explanation": explanation,
                "stats": {
                    "fetches": target.fetches,
                    "changes": target.changes,
                    "aliases": len(target.aliases),
                },
            }

    def _record_fetch(self, target, day, now):
        target.fetches += 1
        target.last_fetch = now
        day.fetches += 1
        # 同一内容目标一天内被抓取多次即为重复抓取：别名未合并时会显著偏高。
        if target.key in day.fetched_keys:
            day.duplicate_fetches += 1
        else:
            day.fetched_keys.add(target.key)

    def observe(self, url, content_digest, now=None):
        """抓取后报告内容摘要；变化频繁的页面缩短间隔，稳定页面放宽。

        内容摘要只影响该目标下一次的到期时间，不参与 URL 判重。
        """
        with self._lock:
            site = site_of(url)
            canonical, _ = canonicalize(url, self.rules_for(site))
            target = self._targets.get(_hash_key(canonical))
            if target is None:
                return {"known": False, "fingerprint": _hash_key(canonical)}

            old = target.interval
            if target.last_digest is None:
                target.last_digest = content_digest
                changed = None
            elif content_digest == target.last_digest:
                target.interval = min(MAX_INTERVAL, target.interval * GROW_FACTOR)
                changed = False
            else:
                target.last_digest = content_digest
                target.changes += 1
                target.interval = max(MIN_INTERVAL, target.interval * SHRINK_FACTOR)
                changed = True

            when = now if now is not None else (target.last_fetch or 0)
            day = self._day(target.site, when)
            if changed is False:
                day.unchanged_fetches += 1

            if changed is None:
                why = "baseline recorded"
            elif changed:
                why = "content changed; revisit sooner"
            else:
                why = "content unchanged; revisit later"
            return {
                "known": True,
                "fingerprint": target.key,
                "changed": changed,
                "is_duplicate": changed is False,
                "interval_before": old,
                "interval_after": target.interval,
                "next_due_at": (
                    target.last_fetch + target.interval
                    if target.last_fetch is not None else None
                ),
                "explanation": why,
            }

    # ------------------------------------------------------------------
    # 历史链接重新判定
    # ------------------------------------------------------------------
    def rejudge(self, rules=None, site=None):
        """按新规则重建历史目标的判重键；同键目标合并，历史计数保留。

        ``site`` 为空时处理全部站点；指定站点时只影响该站点的采集任务。
        """
        # 指定站点时默认用该站点当前生效的规则（可能是试运行规则），
        # 避免试运行期间按全局默认规则误判。
        effective = rules or (
            self.rules_for(site) if site else self.default_rules
        )
        with self._lock:
            source = [
                t for t in self._targets.values()
                if site is None or t.site == site
            ]
            if not source:
                return {
                    "ruleset": effective.version, "site": site,
                    "targets_before": 0, "targets_after": 0, "merged": 0,
                }

            # 记录每个源目标贡献的地址与观测信息，重建时保持入队先后。
            observations = []
            for target in source:
                urls = [(
                    target.first_url, {
                        "first_seen": target.first_seen,
                        "last_seen": target.last_seen,
                        "seen_count": 1,
                        "rules_hit": target.rules_hit,
                    },
                )]
                urls.extend((url, info) for url, info in target.aliases.items())
                for alias_url, info in urls:
                    canonical, detail = canonicalize(alias_url, effective)
                    observations.append((
                        info["first_seen"], alias_url, _hash_key(canonical),
                        rule_hits(detail), info, target,
                    ))
            observations.sort(key=lambda item: item[0])

            for target in source:
                self._targets.pop(target.key, None)

            groups = {}
            for first_seen, alias_url, key, hits, info, source_target in observations:
                group = groups.setdefault(key, {
                    "first_url": alias_url, "first_seen": first_seen,
                    "last_seen": info["last_seen"], "urls": {},
                    "last_fetch": None, "fetches": 0, "changes": 0,
                    "interval": BASE_INTERVAL, "last_digest": None,
                    "rules_hit": hits, "sites": set(), "sources": set(),
                })
                group["sites"].add(source_target.site)
                group["sources"].add(id(source_target))
                group["last_seen"] = max(group["last_seen"], info["last_seen"])
                existing = group["urls"].get(alias_url)
                if existing is None:
                    group["urls"][alias_url] = {
                        "first_seen": info["first_seen"],
                        "last_seen": info["last_seen"],
                        "seen_count": info.get("seen_count", 1),
                        "rules_hit": hits,
                    }
                else:
                    existing["last_seen"] = max(
                        existing["last_seen"], info["last_seen"]
                    )

            # 同一源目标只计一次历史；若它被拆分到多个新组，历史各计一次。
            # 合并后取最短的已知间隔，避免把更新频繁的目标拖慢。
            for group in groups.values():
                known_intervals = []
                for source_target in source:
                    if id(source_target) not in group["sources"]:
                        continue
                    group["fetches"] += source_target.fetches
                    group["changes"] += source_target.changes
                    if source_target.last_fetch is not None:
                        known_intervals.append(source_target.interval)
                    if source_target.last_fetch is not None and (
                        group["last_fetch"] is None
                        or source_target.last_fetch > group["last_fetch"]
                    ):
                        group["last_fetch"] = source_target.last_fetch
                        group["last_digest"] = source_target.last_digest
                if known_intervals:
                    group["interval"] = min(known_intervals)

            for key, group in groups.items():
                first_url = group["first_url"]
                aliases = {
                    url: info for url, info in group["urls"].items()
                    if url != first_url
                }
                target = Target(
                    key=key,
                    site=site or sorted(group["sites"])[0],
                    first_url=first_url,
                    first_seen=group["first_seen"],
                    last_seen=group["last_seen"],
                    last_fetch=group["last_fetch"],
                    fetches=group["fetches"],
                    changes=group["changes"],
                    interval=group["interval"],
                    last_digest=group["last_digest"],
                    ruleset=effective.version,
                    rules_hit=group["rules_hit"],
                    aliases=aliases,
                )
                self._targets[key] = target

            return {
                "ruleset": effective.version,
                "site": site,
                "targets_before": len(source),
                "targets_after": len(groups),
                "merged": sum(
                    len(group["sources"]) - 1
                    for group in groups.values()
                    if len(group["sources"]) > 1
                ),
                "split": sum(
                    1 for source_target in source
                    if sum(
                        1 for group in groups.values()
                        if id(source_target) in group["sources"]
                    ) > 1
                ),
            }

    # ------------------------------------------------------------------
    # 告警与样本导出
    # ------------------------------------------------------------------
    def alias_alerts(self, site=None, min_aliases=None, min_ratio=None):
        """别名过多的站点；附带可直接排查的样本。"""
        min_aliases = self.alert_min_aliases if min_aliases is None else min_aliases
        min_ratio = self.alert_min_ratio if min_ratio is None else min_ratio
        with self._lock:
            alerts = []
            for name, stats in self._site_alias_stats(site).items():
                if stats["alias_variants"] < min_aliases:
                    continue
                if stats["alias_ratio"] < min_ratio:
                    continue
                alerts.append({
                    "site": name,
                    "targets": stats["targets"],
                    "alias_variants": stats["alias_variants"],
                    "alias_ratio": round(stats["alias_ratio"], 4),
                    "samples": self._alias_samples(name, limit=5, threshold=0),
                })
            alerts.sort(key=lambda item: item["alias_variants"], reverse=True)
            return alerts

    def export_alias_samples(self, path, site=None, limit=20, min_aliases=None):
        """导出别名样本 JSON；返回写入的结构，也可用于人工核查。"""
        min_aliases = self.alert_min_aliases if min_aliases is None else min_aliases
        with self._lock:
            alerts = self.alias_alerts(site=site, min_aliases=min_aliases)
            payload = {
                "generated_at": int(time.time()),
                "alerts": alerts,
                "samples": self._alias_samples(site, limit=limit, threshold=min_aliases),
            }
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            return payload

    def _site_alias_stats(self, site=None):
        stats = {}
        for target in self._targets.values():
            if site is not None and target.site != site:
                continue
            entry = stats.setdefault(target.site, {
                "targets": 0, "alias_variants": 0, "urls": 0,
            })
            entry["targets"] += 1
            entry["alias_variants"] += len(target.aliases)
            entry["urls"] += 1 + len(target.aliases)
        for entry in stats.values():
            total = entry["targets"] + entry["alias_variants"]
            entry["alias_ratio"] = (
                entry["alias_variants"] / total if total else 0.0
            )
        return stats

    def _alias_samples(self, site=None, limit=20, threshold=0):
        samples = []
        for target in sorted(self._targets.values(),
                             key=lambda t: len(t.aliases), reverse=True):
            if len(target.aliases) < max(threshold, 1):
                continue
            if site is not None and target.site != site:
                continue
            samples.append({
                "site": target.site,
                "fingerprint": target.key,
                "kept_url": target.first_url,
                "first_seen": target.first_seen,
                "aliases": [
                    {
                        "url": url,
                        "seen_count": info["seen_count"],
                        "rules_hit": info["rules_hit"],
                    }
                    for url, info in target.aliases.items()
                ],
            })
            if len(samples) >= limit:
                break
        return samples

    # ------------------------------------------------------------------
    # 按天观测
    # ------------------------------------------------------------------
    def metrics(self, site=None, day=None):
        """返回按站点/日聚合的别名比例与重复抓取次数。"""
        with self._lock:
            result = {}
            for (name, day_key), state in self._days.items():
                if site is not None and name != site:
                    continue
                if day is not None and day_key != day:
                    continue
                urls_seen = len(state.urls_seen)
                alias_urls = len(state.alias_urls)
                result.setdefault(name, {})[day_key] = {
                    "urls_seen": urls_seen,
                    "new_targets": state.new_targets,
                    "alias_urls": alias_urls,
                    "alias_ratio": (
                        round(alias_urls / urls_seen, 4) if urls_seen else 0.0
                    ),
                    "fetches": state.fetches,
                    "duplicate_fetches": state.duplicate_fetches,
                    "unchanged_fetches": state.unchanged_fetches,
                    "rulesets": sorted(state.rulesets),
                }
            return result

    def daily_series(self, site):
        """按日期升序返回该站点的每日指标，便于试运行前后对比。"""
        days = self.metrics(site=site).get(site, {})
        return [dict(day=day, **values) for day, values in sorted(days.items())]

    def _day(self, site, now):
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
        return self._days.setdefault((site, day_key), SiteDay())

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------
    def explain(self, url, now=None):
        """只做判定而不改变状态，用于解释某个地址会命中什么。"""
        with self._lock:
            site = site_of(url)
            effective = self.rules_for(site)
            canonical, detail = canonicalize(url, effective)
            key = _hash_key(canonical)
            target = self._targets.get(key)
            result = {
                "requested_url": url,
                "fingerprint": key,
                "canonical": canonical,
                "site": site,
                "ruleset": effective.version,
                "rules_hit": rule_hits(detail),
                "removed_params": detail["removed_params"],
                "known": target is not None,
                "kept_url": target.first_url if target else None,
                "is_alias": (
                    target is not None and url.strip() != target.first_url
                ),
            }
            if target and now is not None:
                last = (target.last_fetch if target.last_fetch is not None
                        else target.first_seen)
                result["due"] = now - last >= target.interval
                result["interval"] = target.interval
                result["next_due_at"] = last + target.interval
            return result


def _hash_key(canonical):
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


DEFAULT_STORE = LinkStore()
