"""按站点汇总识别情况，并支持新语言站点接入前的样本评估。

新站点接入流程：先跑一批样本 -> evaluate_onboarding 判断主语言是否稳定、
低把握比例是否可接受 -> 稳定后才开量。
"""

from collections import defaultdict


class SiteStats:
    def __init__(self):
        self.pages = 0
        self._by_site = defaultdict(lambda: {
            "pages": 0,
            "languages": defaultdict(int),
            "confidence": defaultdict(int),
            "low_confidence": 0,
            "overrides": 0,
            "declaration_conflicts": 0,
            "multilingual": 0,
        })

    def add(self, result):
        """录入一条 detect/覆盖后的结果。"""
        site = result.get("site") or "unknown"
        bucket = self._by_site[site]
        bucket["pages"] += 1
        self.pages += 1
        bucket["languages"][result["language"]] += 1
        bucket["confidence"][result["confidence"]] += 1
        if result["confidence"] == "low":
            bucket["low_confidence"] += 1
        if "override" in result:
            bucket["overrides"] += 1
        if result.get("declaration_conflict"):
            bucket["declaration_conflicts"] += 1
        if result.get("multilingual"):
            bucket["multilingual"] += 1

    def summary(self, site=None):
        """单站点或全量汇总。统计口径按站点组织。"""
        if site is not None:
            return self._one(site, self._by_site.get(site))
        return {name: self._one(name, data)
                for name, data in sorted(self._by_site.items())}

    def _one(self, name, data):
        if data is None:
            return None
        pages = data["pages"]
        dominant_lang = max(data["languages"], key=data["languages"].get)
        return {
            "site": name,
            "pages": pages,
            "dominant_language": dominant_lang,
            "dominant_ratio": round(data["languages"][dominant_lang] / pages, 4),
            "language_distribution": dict(data["languages"]),
            "confidence_distribution": dict(data["confidence"]),
            "low_confidence_rate": round(data["low_confidence"] / pages, 4),
            "override_rate": round(data["overrides"] / pages, 4),
            "declaration_conflict_rate":
                round(data["declaration_conflicts"] / pages, 4),
            "multilingual_rate": round(data["multilingual"] / pages, 4),
        }


# 接入评估阈值
MIN_SAMPLE_PAGES = 20
MIN_DOMINANT_RATIO = 0.9
MAX_LOW_CONFIDENCE_RATE = 0.1
MAX_CONFLICT_RATE = 0.05


def evaluate_onboarding(results, expected_language=None):
    """新语言站点开量前的样本评估。

    要求：样本量足够、主语言稳定（占比高且与预期一致）、
    低把握与声明冲突比例受控。返回是否可开量及各项指标。
    """
    stats = SiteStats()
    sites = set()
    for result in results:
        stats.add(result)
        sites.add(result.get("site") or "unknown")

    if len(sites) != 1:
        raise ValueError("接入评估应针对单个站点，收到 %d 个站点" % len(sites))
    site = next(iter(sites))
    summary = stats.summary(site)

    checks = {
        "enough_samples": summary["pages"] >= MIN_SAMPLE_PAGES,
        "dominant_stable": summary["dominant_ratio"] >= MIN_DOMINANT_RATIO,
        "low_confidence_controlled":
            summary["low_confidence_rate"] <= MAX_LOW_CONFIDENCE_RATE,
        "declaration_conflict_controlled":
            summary["declaration_conflict_rate"] <= MAX_CONFLICT_RATE,
    }
    if expected_language is not None:
        checks["matches_expected"] = \
            summary["dominant_language"] == expected_language

    return {
        "site": site,
        "ready": all(checks.values()),
        "checks": checks,
        "thresholds": {
            "min_sample_pages": MIN_SAMPLE_PAGES,
            "min_dominant_ratio": MIN_DOMINANT_RATIO,
            "max_low_confidence_rate": MAX_LOW_CONFIDENCE_RATE,
            "max_conflict_rate": MAX_CONFLICT_RATE,
        },
        "summary": summary,
    }
