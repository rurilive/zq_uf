"""自适应并发目录探测：跟着目标反馈调速，拦截页不计命中，支持断点续跑。"""

_SITES = {}
_STATE = {}

_HIT_STATUS = (200, 301, 302, 401, 403)
_BLOCK_STATUS = (429, 503)
_BLOCK_MARKERS = (
    "too many requests", "rate limit", "access denied", "blocked",
    "captcha", "challenge", "waf",
)
_REFUSED_MARKERS = ("refused", "reset", "unreachable")
_REFUSED_STREAK_LIMIT = 3  # 连续被拒达到此次数视为站点整体拒绝访问
_SLOW_MS = 1000            # 响应耗时超过该值视为变慢
_RECOVER_AFTER = 4         # 连续正常响应达到此次数后并发 +1

_BACKUP_EXT = (".bak", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
               ".sql", ".old", ".swp", ".orig")
_ADMIN_WORDS = ("admin", "login", "manage", "dashboard", "console",
                "phpmyadmin", "backend")

_STATUS_RANK = {200: 0, 301: 1, 302: 2, 401: 3, 403: 4}


def load(site, words, responses):
    """登记站点、字典和每个路径的响应；重新载入会清空该站进度。"""
    if not isinstance(site, str) or not site:
        raise ValueError("site 不能为空")
    if not isinstance(words, list) or not words:
        raise ValueError("字典不能为空")
    if not isinstance(responses, dict):
        raise ValueError("responses 必须是字典")
    _SITES[site] = {"words": list(words), "responses": dict(responses)}
    _STATE.pop(site, None)
    return {"site": site, "words": len(words)}


def update(site, responses):
    """目标状态变化后更新部分响应，保留已完成的进度。"""
    data = _SITES.get(site)
    if data is None:
        raise ValueError("站点未载入")
    if not isinstance(responses, dict):
        raise ValueError("responses 必须是字典")
    data["responses"].update(responses)
    return {"site": site, "responses": len(data["responses"])}


def _outcome(response):
    """把一次响应归类为 refused / blocked / hit / miss。"""
    error = str(response.get("error", "")).lower()
    if any(m in error for m in _REFUSED_MARKERS):
        return {"kind": "refused"}
    status = response.get("status", 404)
    body = str(response.get("body", "")).lower()
    if status in _BLOCK_STATUS or any(m in body for m in _BLOCK_MARKERS):
        return {"kind": "blocked"}
    slow = response.get("latency_ms", 0) >= _SLOW_MS
    if status in _HIT_STATUS:
        return {"kind": "hit", "status": status, "slow": slow}
    return {"kind": "miss", "status": status, "slow": slow}


def _category(path):
    """命中路径归类：备份、管理入口、目录、文件。"""
    p = path.lower()
    name = p.rsplit("/", 1)[-1]
    if name.endswith(_BACKUP_EXT) or "backup" in name or "dump" in name:
        return "backup"
    if any(w in p for w in _ADMIN_WORDS):
        return "admin"
    if p.endswith("/") or "." not in name:
        return "dir"
    return "file"


def _merged_hits(confirmed):
    """汇总命中；同一路径的不同参数合并成一条。"""
    merged = {}
    order = []
    for word, r in confirmed.items():
        if not r["hit"]:
            continue
        base, _, qs = word.partition("?")
        entry = merged.get(base)
        if entry is None:
            entry = {"path": base, "status": r["status"],
                     "params": [], "category": _category(base)}
            merged[base] = entry
            order.append(base)
        elif _STATUS_RANK.get(r["status"], 9) < _STATUS_RANK.get(entry["status"], 9):
            entry["status"] = r["status"]
        if qs and qs not in entry["params"]:
            entry["params"].append(qs)
    return [merged[b] for b in order]


def _resume(words, state):
    """第一个未确认路径的下标；全部确认后为字典长度。"""
    for i, w in enumerate(words):
        if w not in state["confirmed"]:
            return i
    return len(words)


def probe(site, concurrency=8):
    """跑一轮探测：被拦截或变慢就降并发，恢复后逐步提回；整体拒绝则停本轮。"""
    data = _SITES.get(site)
    if data is None:
        raise ValueError("站点未载入")
    state = _STATE.get(site)
    if state is None:
        state = _STATE[site] = {
            "confirmed": {},       # word -> {"hit": bool, "status": int}
            "blocked": [],         # 被拦截、尚未确认的路径
            "concurrency": concurrency,
            "max_concurrency": concurrency,
            "stopped": False,
            "refused": False,
        }
    words = data["words"]
    responses = data["responses"]
    current = state["concurrency"]
    clean_streak = 0
    refused_streak = 0
    state["stopped"] = False
    state["refused"] = False

    for word in words:
        if word in state["confirmed"]:
            continue  # 已确认的路径不再重复请求
        outcome = _outcome(responses.get(word) or {})
        kind = outcome["kind"]
        if kind == "refused":
            refused_streak += 1
            clean_streak = 0
            if refused_streak >= _REFUSED_STREAK_LIMIT:
                state["stopped"] = True
                state["refused"] = True
                break
            continue
        refused_streak = 0
        if kind == "blocked":
            if word not in state["blocked"]:
                state["blocked"].append(word)
            current = max(1, current // 2)
            clean_streak = 0
            continue
        state["confirmed"][word] = {"hit": kind == "hit", "status": outcome["status"]}
        if word in state["blocked"]:
            state["blocked"].remove(word)
        if outcome.get("slow"):
            current = max(1, current // 2)
            clean_streak = 0
        else:
            clean_streak += 1
            if clean_streak >= _RECOVER_AFTER and current < state["max_concurrency"]:
                current += 1
                clean_streak = 0

    state["concurrency"] = current
    hits = _merged_hits(state["confirmed"])
    state["hits"] = hits
    return {
        "site": site,
        "hits": [dict(h) for h in hits],
        "blocked": len(state["blocked"]),
        "concurrency": current,
        "resume": _resume(words, state),
        "stopped": state["stopped"],
    }


def report(site):
    """按站点汇总进度：命中归类、被拦截批次单独列出。"""
    state = _STATE.get(site)
    if state is None:
        raise ValueError("站点未扫过")
    words = _SITES[site]["words"]
    groups = {"dir": [], "file": [], "backup": [], "admin": []}
    for hit in state["hits"]:
        groups[hit["category"]].append(hit["path"])
    return {
        "site": site,
        "hits": len(state["hits"]),
        "blocked": len(state["blocked"]),
        "blocked_paths": list(state["blocked"]),
        "concurrency": state["concurrency"],
        "resume": _resume(words, state),
        "total": len(words),
        "done": len(state["confirmed"]),
        "stopped": state["stopped"],
        "refused": state["refused"],
        "groups": groups,
    }
