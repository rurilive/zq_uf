"""选择器巡检：按站点和字段维护命中率与结构基线，异常时分级告警。"""

from collections import Counter, deque

BASELINE_WINDOW = 20
HIT_RATE_FLOOR = 0.5
STRUCTURE_CHANGE_RATIO = 0.0
ESCALATE_AFTER = 2
SILENCE_CHECKS = 2
SAMPLE_LIMIT = 3

_baselines = {}
_check_seq = {}
_silence = {}
_alerts = {}
_selectors = {}
_shared_selectors = {}


def reset():
    """清空全部基线、告警、静默状态和选择器配置。"""
    for state in (_baselines, _check_seq, _silence, _alerts,
                  _selectors, _shared_selectors):
        state.clear()


def set_selectors(mapping, site=None):
    """配置字段对应的选择器；site 为 None 时所有站点共用。"""
    target = _shared_selectors if site is None else _selectors.setdefault(site, {})
    target.update(mapping)


def _selector_for(site, field):
    return (_selectors.get(site, {}).get(field)
            or _shared_selectors.get(field) or field)


def _signature(value):
    length = len(value)
    bucket = "short" if length < 8 else "medium" if length < 32 else "long"
    digits = sum(1 for ch in value if ch.isdigit())
    ratio = digits / length
    shape = "digit" if ratio > 0.6 else "alpha" if ratio < 0.2 else "mixed"
    return (bucket, shape)


def _streak(indices):
    best = current = 0
    previous = None
    for index in indices:
        current = current + 1 if previous is not None and index == previous + 1 else 1
        best = max(best, current)
        previous = index
    return best


def _evaluate(site, field, key, seq, baseline, per_page, alerts):
    base_hit_rate = sum(hit for hit, _ in baseline) / len(baseline)
    signatures = Counter(sig for hit, sig in baseline if hit)
    base_signature = signatures.most_common(1)[0][0] if signatures else None
    total = len(per_page)
    hit_rate = sum(hit for hit, _ in per_page) / total
    misses = [i for i, (hit, _) in enumerate(per_page) if not hit]
    mismatches = [i for i, (hit, sig) in enumerate(per_page)
                  if hit and sig != base_signature]
    hit_anomaly = base_hit_rate > 0 and hit_rate < base_hit_rate * HIT_RATE_FLOOR
    hit_count = total - len(misses)
    struct_anomaly = (bool(mismatches)
                      and len(mismatches) / max(hit_count, 1) > STRUCTURE_CHANGE_RATIO)
    if not (hit_anomaly or struct_anomaly):
        return
    failing = sorted((misses if hit_anomaly else [])
                     + (mismatches if struct_anomaly else []))
    level = "escalated" if _streak(failing) >= ESCALATE_AFTER else "alert"
    reasons = []
    if hit_anomaly:
        reasons.append("hit_rate")
    if struct_anomaly:
        reasons.append("structure")
    last = _silence.get(key)
    silenced = last is not None and seq - last <= SILENCE_CHECKS
    alert = _alerts.get(key)
    if alert is None:
        alert = {"site": site, "field": field,
                 "selector": _selector_for(site, field), "repeats": 0}
        _alerts[key] = alert
    alert.update({"reasons": reasons, "level": level,
                  "hit_rate": round(hit_rate, 3),
                  "baseline": round(base_hit_rate, 3),
                  "samples": failing[:SAMPLE_LIMIT]})
    alert["repeats"] += 1
    if not silenced:
        _silence[key] = seq
        alerts.append(alert)


def check(site, pages):
    """巡检一批页面，返回命中总数和按站点/字段的告警。"""
    seq = _check_seq.get(site, 0) + 1
    _check_seq[site] = seq
    hits = sum(1 for page in pages for value in page.values() if value)
    result = {"site": site, "hits": hits, "pages": len(pages), "alerts": []}
    if not pages:
        return result

    values = {}
    for index, page in enumerate(pages):
        for field, value in page.items():
            values.setdefault(field, {})[index] = value
    for known_site, known_field in _baselines:
        if known_site == site:
            values.setdefault(known_field, {})

    for field in sorted(values):
        key = (site, field)
        per_page = []
        for index in range(len(pages)):
            value = values[field].get(index)
            per_page.append((bool(value), _signature(value) if value else None))
        baseline = _baselines.get(key)
        if baseline:
            _evaluate(site, field, key, seq, baseline, per_page, result["alerts"])
        _baselines.setdefault(key, deque(maxlen=BASELINE_WINDOW)).extend(per_page)
    return result
