"""采集字段提取与字段级来源记录。"""

import json

DEFAULT_SNIPPET_RADIUS = 30


class ExtractResult(dict):
    """字段 -> 主值；每个字段的全部候选和来源在 sources 里。"""

    def __init__(self, values, sources):
        super().__init__(values)
        self.sources = sources

    def to_dict(self, max_snippet=None, max_candidates=None):
        """转成可 JSON 序列化的普通 dict，可截断片段和候选数以控制体积。"""
        sources = {}
        for field, source in self.sources.items():
            candidates = source["candidates"]
            if max_candidates is not None:
                candidates = candidates[:max_candidates]
            sources[field] = {
                "value": source["value"],
                "rule": source["rule"],
                "candidates": [_trim_candidate(c, max_snippet) for c in candidates],
            }
        return {"values": dict(self), "sources": sources}

    def to_json(self, **kwargs):
        return json.dumps(self.to_dict(**kwargs), ensure_ascii=False, default=str)


def _trim_candidate(candidate, max_snippet):
    if max_snippet is None or len(candidate["snippet"]) <= max_snippet:
        return dict(candidate)
    trimmed = dict(candidate)
    trimmed["snippet"] = candidate["snippet"][:max_snippet]
    trimmed["truncated"] = True
    return trimmed


def _rule_meta(rule, index):
    """规则身份：id 稳定（改名可追溯），version 随规则演进。"""
    return {
        "id": rule.get("id") or rule.get("name") or rule["field"],
        "name": rule.get("name") or rule.get("id") or rule["field"],
        "version": str(rule.get("version", "1")),
        "priority": rule.get("priority", index),
    }


def _locate(page, value, radius):
    """在页面的字符串字段里定位值，返回（上下文片段, 位置）。"""
    if isinstance(value, str) and value:
        for key, raw in page.items():
            if not isinstance(raw, str):
                continue
            start = raw.find(value)
            if start >= 0:
                end = start + len(value)
                snippet = raw[max(0, start - radius):end + radius]
                return snippet, {"key": key, "start": start, "end": end}
    return str(value)[: 2 * radius], None


def _pick_primary(candidates):
    """优先级高的为主值；并列时后命中的覆盖前面的。"""
    primary = None
    for candidate in candidates:
        if primary is None or candidate["priority"] >= primary["priority"]:
            primary = candidate
    return primary


def _build(fields):
    values, sources = {}, {}
    for field, candidates in fields.items():
        primary = _pick_primary(candidates)
        values[field] = primary["value"]
        sources[field] = {
            "value": primary["value"],
            "rule": {k: primary["rule"][k] for k in ("id", "name", "version")},
            "candidates": candidates,
        }
    return ExtractResult(values, sources)


def extract(page, rules, page_id=None, snippet_radius=DEFAULT_SNIPPET_RADIUS):
    """按规则提取字段，返回 ExtractResult（可直接当字段->主值的 dict 用）。"""
    fields = {}
    for index, rule in enumerate(rules):
        value = rule["get"](page)
        if value is None or value == "":
            continue
        meta = _rule_meta(rule, index)
        snippet, position = _locate(page, value, snippet_radius)
        candidate = {
            "rule": {k: meta[k] for k in ("id", "name", "version")},
            "value": value,
            "snippet": snippet,
            "position": position,
            "page": page_id,
            "priority": meta["priority"],
        }
        fields.setdefault(rule["field"], []).append(candidate)
    return _build(fields)


def extract_many(pages, rules, snippet_radius=DEFAULT_SNIPPET_RADIUS):
    """跨页提取并合并，每条候选带来源页（page.id 或页序号）。"""
    results = [
        extract(page, rules, page_id=page.get("id", i), snippet_radius=snippet_radius)
        for i, page in enumerate(pages)
    ]
    return merge(results)


def merge(results):
    """合并多个 extract 结果，候选保留各自来源页，主值按优先级重选。"""
    fields = {}
    for result in results:
        for field, source in result.sources.items():
            fields.setdefault(field, []).extend(source["candidates"])
    return _build(fields)
