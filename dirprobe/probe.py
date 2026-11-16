"""固定并发扫目录，拦截页也照常记成命中。"""

_SITES = {}
_STATE = {}


def load(site, words, responses):
    """登记站点、字典和每个路径的响应。"""
    if not isinstance(site, str) or not site:
        raise ValueError("site 不能为空")
    if not isinstance(words, list) or not words:
        raise ValueError("字典不能为空")
    if not isinstance(responses, dict):
        raise ValueError("responses 必须是字典")
    _SITES[site] = {"words": list(words), "responses": dict(responses)}
    return {"site": site, "words": len(words)}


def probe(site, concurrency=8):
    """一次跑完全部路径，遇到拦截也不降速。"""
    data = _SITES.get(site)
    if data is None:
        raise ValueError("站点未载入")
    hits = []
    blocked = 0
    for word in data["words"]:
        response = data["responses"].get(word, {})
        status = response.get("status", 404)
        if status in (200, 301, 403, 429):
            hits.append({"path": word, "status": status})
            if status == 429:
                blocked += 1
    _STATE[site] = {"hits": hits, "blocked": blocked, "concurrency": concurrency}
    return {
        "site": site,
        "hits": [dict(hit) for hit in hits],
        "blocked": blocked,
        "concurrency": concurrency,
        "resume": 0,
        "stopped": False,
    }


def report(site):
    """按站点汇总本轮结果。"""
    state = _STATE.get(site)
    if state is None:
        raise ValueError("站点未扫过")
    return {
        "site": site,
        "hits": len(state["hits"]),
        "blocked": state["blocked"],
        "concurrency": state["concurrency"],
        "resume": 0,
        "groups": {},
    }
