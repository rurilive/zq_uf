"""采集页面软 404 判定：状态、内容、模板三个维度一起判。

判定规则：
- 状态码非 200：立即判为 soft404（标记 bad_status）。
- 状态码 200 但正文为空 / 命中占位文案 / 跳转异常：先标记
  （empty_body / placeholder_text / abnormal_redirect），单页不升级。
- 同一 (site, template) 连续命中内容信号达到 escalate_after 页，
  升级为 soft404（追加 template_pattern 标记），此后该模板命中即升级。
- 正常页面会重置该模板的连续计数。

阈值可按站点覆盖（configure / load_config），误判通过白名单回退，
不需要改代码。
"""

import json
import re
from datetime import date
from urllib.parse import urlparse

_DEFAULTS = {
    # 正文去空白后短于该长度视为空页
    "min_text_len": 1,
    # 占位文案（大小写不敏感的子串匹配）
    "placeholder_phrases": [
        "暂无内容", "暂无数据", "页面不存在", "内容不存在", "页面走丢",
        "正在建设", "敬请期待", "page not found", "not found",
        "coming soon", "under construction", "no results",
    ],
    # 跳转链超过该长度视为异常跳转
    "redirect_max": 3,
    # 跳转目标路径命中这些片段视为异常（含跳回首页）
    "redirect_bad_patterns": [
        "/404", "/error", "/not-found", "/notfound", "/login", "/unavailable",
    ],
    # 同站点同模板连续命中内容信号多少页后升级为 soft404
    "escalate_after": 3,
    # 误判回退：模板 / URL 前缀白名单，命中即按正常页处理
    "allow_templates": [],
    "allow_url_prefixes": [],
    # 样本中保留的正文摘要长度
    "sample_text_len": 120,
    # 每个站点 / 模板最多保留的命中样本数
    "max_samples": 50,
}

_CONFIG = {"default": dict(_DEFAULTS), "sites": {}}

_stats = {}        # site -> {"total", "soft404"}
_day_stats = {}    # (site, day) -> {"total", "soft404"}
_tpl_stats = {}    # (site, template) -> {"total", "soft404", "flagged"}
_consecutive = {}  # (site, template) -> 连续内容信号命中数
_confirmed = set()  # 已确认模板级软 404 的 (site, template)
_flagged = {}      # (site, template) -> 最近连续命中的样本（升级时作为证据）
_samples = {}      # site -> [sample, ...] 命中样本
_tpl_samples = {}  # (site, template) -> [sample, ...]


def configure(site=None, **overrides):
    """覆盖判定阈值。site 为 None 时改全局默认，否则只影响该站点。"""
    unknown = set(overrides) - set(_DEFAULTS)
    if unknown:
        raise ValueError("unknown config keys: %s" % sorted(unknown))
    target = _CONFIG["default"] if site is None else \
        _CONFIG["sites"].setdefault(site, {})
    target.update(overrides)


def load_config(path):
    """从 JSON 文件加载配置：{"default": {...}, "sites": {"站点": {...}}}。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key, value in data.get("default", {}).items():
        configure(None, **{key: value})
    for site, overrides in data.get("sites", {}).items():
        configure(site, **overrides)


def reset():
    """清空全部统计与配置（测试和重跑用）。"""
    _CONFIG["default"] = dict(_DEFAULTS)
    _CONFIG["sites"] = {}
    for store in (_stats, _day_stats, _tpl_stats, _consecutive,
                  _flagged, _samples, _tpl_samples):
        store.clear()
    _confirmed.clear()


def _conf(site):
    merged = dict(_CONFIG["default"])
    merged.update(_CONFIG["sites"].get(site, {}))
    return merged


def _bump(store, key, field):
    entry = store.setdefault(key, {"total": 0, "soft404": 0, "flagged": 0})
    entry["total"] += 1
    if field:
        entry[field] += 1
    return entry


def _keep(store, key, sample, cap):
    buf = store.setdefault(key, [])
    buf.append(sample)
    del buf[:-cap]
    return buf


def _check_redirect(redirect, conf):
    """跳转异常返回原因 dict，否则返回 None。"""
    if not redirect:
        return None
    chain = list(redirect) if isinstance(redirect, (list, tuple)) else [redirect]
    final = str(chain[-1])
    if len(chain) > conf["redirect_max"]:
        return {"final": final, "reason": "chain_too_long", "hops": len(chain)}
    path = (urlparse(final).path or "/").lower()
    if path == "/":
        return {"final": final, "reason": "redirect_to_root"}
    for pat in conf["redirect_bad_patterns"]:
        if pat in path:
            return {"final": final, "reason": "redirect_to_error_page",
                    "pattern": pat}
    return None


def judge(page):
    """判定单页，返回 kind、marks、证据 evidence 和命中样本 samples。"""
    url = page.get("url", "")
    site = page.get("site", "")
    status = page.get("status", 0)
    text = page.get("text") or ""
    template = page.get("template") or ""
    day = page.get("day") or date.today().isoformat()
    conf = _conf(site)

    marks = []
    evidence = {"status": status, "template": template, "day": day}
    stripped = re.sub(r"\s+", " ", text).strip()

    allowed = (
        template in conf["allow_templates"]
        or any(url.startswith(p) for p in conf["allow_url_prefixes"])
    )

    if status != 200:
        marks.append("bad_status")
        evidence["bad_status"] = status
    elif not allowed:
        if len(stripped) < conf["min_text_len"]:
            marks.append("empty_body")
            evidence["empty_body"] = True
        else:
            low = stripped.lower()
            hit = next((p for p in conf["placeholder_phrases"]
                        if p.lower() in low), None)
            if hit:
                marks.append("placeholder_text")
                evidence["placeholder_text"] = hit
        redirect = _check_redirect(page.get("redirect"), conf)
        if redirect:
            marks.append("abnormal_redirect")
            evidence["abnormal_redirect"] = redirect

    key = (site, template)
    content_flagged = bool(marks) and "bad_status" not in marks
    if "bad_status" in marks:
        kind = "soft404"
    elif content_flagged:
        consecutive = _consecutive.get(key, 0) + 1
        _consecutive[key] = consecutive
        if key in _confirmed or consecutive >= conf["escalate_after"]:
            _confirmed.add(key)
            kind = "soft404"
            marks.append("template_pattern")
            evidence["template_pattern"] = {
                "consecutive": consecutive,
                "threshold": conf["escalate_after"],
            }
        else:
            kind = "ok"
    else:
        _consecutive[key] = 0
        _flagged.pop(key, None)
        kind = "ok"

    sample = {
        "url": url,
        "status": status,
        "headers": dict(page.get("headers") or {}),
        "text_snippet": stripped[:conf["sample_text_len"]],
        "template": template,
        "day": day,
        "marks": list(marks),
    }

    if content_flagged:
        _keep(_flagged, key, sample, max(conf["escalate_after"], 5))

    samples = []
    if kind == "soft404":
        if "template_pattern" in marks:
            # 模板升级：把连续命中的样本一起作为证据保留
            samples = list(_flagged.get(key, []))
            if sample not in samples:
                samples.append(sample)
            for s in samples:
                if s not in _samples.get(site, []):
                    _keep(_samples, site, s, conf["max_samples"])
                if s not in _tpl_samples.get(key, []):
                    _keep(_tpl_samples, key, s, conf["max_samples"])
        else:
            samples = [sample]
            _keep(_samples, site, sample, conf["max_samples"])
            _keep(_tpl_samples, key, sample, conf["max_samples"])

    _bump(_stats, site, "soft404" if kind == "soft404" else None)
    _bump(_day_stats, (site, day), "soft404" if kind == "soft404" else None)
    tpl_entry = _bump(_tpl_stats, key, "soft404" if kind == "soft404" else None)
    if content_flagged:
        tpl_entry["flagged"] += 1

    return {
        "url": url,
        "kind": kind,
        "marks": marks,
        "evidence": evidence,
        "samples": samples,
    }


def _ratio(entry):
    total = entry["total"]
    return round(entry["soft404"] / total, 4) if total else 0.0


def report(site, by=None):
    """站点统计。by 为 None 返回总计；by="day" 按天下钻；by="template" 按模板下钻。"""
    total = _stats.get(site, {"total": 0, "soft404": 0})
    if by is None:
        return {"site": site, "total": total["total"],
                "soft404": total["soft404"]}
    if by == "day":
        days = {}
        for (s, day), entry in sorted(_day_stats.items(),
                                      key=lambda kv: kv[0][1]):
            if s != site:
                continue
            days[day] = {"total": entry["total"], "soft404": entry["soft404"],
                         "ratio": _ratio(entry)}
        return {"site": site, "total": total["total"],
                "soft404": total["soft404"], "days": days}
    if by == "template":
        templates = {}
        for (s, template), entry in sorted(_tpl_stats.items()):
            if s != site:
                continue
            templates[template] = {
                "total": entry["total"],
                "soft404": entry["soft404"],
                "flagged": entry["flagged"],
                "ratio": _ratio(entry),
                "confirmed": (s, template) in _confirmed,
                "samples": list(_tpl_samples.get((s, template), []))[:5],
            }
        return {"site": site, "total": total["total"],
                "soft404": total["soft404"], "templates": templates}
    raise ValueError("unknown breakdown: %r" % (by,))
