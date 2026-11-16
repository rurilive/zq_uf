"""自适应目录探测。

探测节奏跟随目标反馈调整：

* 响应变慢或出现拦截页（429 / 拦截指纹）时降低并发，恢复后逐步提回来；
* 拦截页不记成命中，被拦截的路径留待后续批次重试；
* 每批结束都把进度落盘，中断后从上次位置续跑，已确认的路径不再重复请求；
* 站点整体拒绝访问时停止本轮并保留已完成进度，拒绝页不算空结果；
* 命中路径按目录 / 文件 / 备份 / 管理入口归类，同一路径的不同参数合并成一条。

probe(site) 与 report(site) 的调用方式保持不变，返回值在原有字段
（hits / blocked / concurrency / resume）之上补充续跑与归类信息。
"""

import copy
import json
import os

# 并发下限：再怎么降速也保留一个并发位。
MIN_CONCURRENCY = 1
# 连续多少个干净批次后，并发回升一档。
RECOVER_BATCHES = 2
# 多少个不同的“新路径”第一次请求就被拦，判定为站点整体拒绝访问。
REFUSE_STRIKES = 3
# 同一路径累计被拦截多少次后最终判定为“确认拦截”，不再重试、不再占住续跑位置。
MAX_BLOCK_RETRIES = 3
# 响应延迟超过该毫秒数视为“变慢”，本批降一档并发。
DEFAULT_SLOW_MS = 1000
# 默认拦截页指纹（小写子串匹配响应体）。
DEFAULT_FINGERPRINTS = (
    "too many requests",
    "rate limit",
    "访问频繁",
    "请求过多",
    "拦截",
    "waf",
    "防火墙",
    "安全验证",
    "captcha",
)
# 记为命中的状态码：403 表示路径存在但禁止访问，与拦截页是两回事；
# 429 属于限流拦截，不在此列。
HIT_STATUSES = (200, 301, 302, 403)
# 分类键，报告里固定输出这四类。
CATEGORIES = ("directory", "file", "backup", "admin")
BACKUP_HINTS = (
    "backup", "bak", ".old", ".zip", ".tar", ".gz",
    ".sql", ".swp", ".dump", "dump", "~",
)
ADMIN_HINTS = (
    "admin", "manage", "manager", "console", "login",
    "phpmyadmin", "backend", "dashboard",
)
DEFAULT_STATE_DIR = os.path.join(".dirprobe_state")

# site -> 站点配置（字典、响应、落盘目录、判定参数）
_SITES = {}
# site -> 可续跑的运行状态（内存镜像，同时落盘）
_STATE = {}


def _state_path(state_dir, site):
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in site)
    return os.path.join(state_dir, f"{safe}.json")


def _blank_state(words):
    return {
        "version": 1,
        "total": len(words),
        "cursor": 0,           # [0, cursor) 都已有确定结论（命中或不存在）
        "pending": [],         # 被拦截、待重试的字典下标
        "hits": {},            # base path -> 合并后的命中条目
        "blocked_count": 0,    # 累计拦截响应次数
        "blocked_paths": [],   # 曾被拦截的路径（去重）
        "block_attempts": {},  # path -> 累计拦截次数，用尽预算后最终判定
        "settled_blocked": [],  # 已最终判定为拦截、不再重试的路径
        "blocked_batches": [],  # 被拦截的批次明细
        "concurrency": 0,           # 0 表示尚未开始，首次 probe 时取调用方传入值
        "max_concurrency": 0,
        "ok_streak": 0,        # 连续干净批次数
        "stopped": False,      # 站点整体拒绝访问时停止本轮
        "requests": 0,         # 累计模拟请求数
        "requested": {},       # path -> 已请求次数
    }


def load(site, words, responses, state_dir=None,
         block_fingerprints=None, slow_ms=DEFAULT_SLOW_MS):
    """登记站点、字典和每个路径的响应，并恢复历史进度。

    responses 以字典条目（路径）为键，值支持：
      status: HTTP 状态码，缺省 404；
      body:   响应体，命中拦截指纹时按拦截处理；
      ms:     响应耗时（毫秒），超过 slow_ms 视为变慢。

    state_dir 为进度落盘目录，缺省用工作目录下的 .dirprobe_state；
    同一站点字典不变时自动从落盘状态续跑，字典变了则重新开始。
    """
    if not isinstance(site, str) or not site:
        raise ValueError("site 不能为空")
    if not isinstance(words, list) or not words:
        raise ValueError("字典不能为空")
    if not isinstance(responses, dict):
        raise ValueError("responses 必须是字典")
    if state_dir is None:
        state_dir = DEFAULT_STATE_DIR
    fingerprints = tuple(DEFAULT_FINGERPRINTS if block_fingerprints is None
                         else block_fingerprints)

    _SITES[site] = {
        "words": list(words),
        "responses": dict(responses),
        "state_dir": state_dir,
        "fingerprints": fingerprints,
        "slow_ms": slow_ms,
    }

    state = _blank_state(words)
    path = _state_path(state_dir, site)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            if isinstance(saved, dict) and saved.get("total") == len(words):
                state.update({k: saved[k] for k in state if k in saved})
                state["pending"] = list(saved.get("pending", []))
        except (OSError, ValueError):
            # 状态文件损坏时退化为全新探测，不让续跑机制把整轮卡死。
            state = _blank_state(words)
    _STATE[site] = state
    return {"site": site, "words": len(words)}


def reset(site):
    """清掉站点的续跑状态（内存与落盘文件），下一轮从头探测。"""
    if site not in _SITES:
        raise ValueError("站点未载入")
    _STATE[site] = _blank_state(_SITES[site]["words"])
    path = _state_path(_SITES[site]["state_dir"], site)
    try:
        os.remove(path)
    except OSError:
        pass
    return {"site": site, "reset": True}


def _save(site):
    cfg = _SITES[site]
    state = _STATE[site]
    os.makedirs(cfg["state_dir"], exist_ok=True)
    path = _state_path(cfg["state_dir"], site)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _split_path(word):
    """拆出基础路径与查询参数名，参数合并时使用。"""
    base, _, query = word.partition("?")
    base = base.split("#", 1)[0]
    params = set()
    if query:
        for pair in query.split("&"):
            if pair:
                params.add(pair.split("=", 1)[0])
    return base, sorted(params)


def _categorize(base):
    """命中路径归类：备份 > 管理入口 > 目录 > 文件。

    目录以结尾斜杠区分（如 assets/）；没有结尾斜杠的无扩展名路径视为
    文件型端点（如 search、api/v1），有扩展名的自然也是文件。
    """
    tail = base.lower().strip("/").split("/")[-1]
    if any(hint in tail for hint in BACKUP_HINTS):
        return "backup"
    if any(hint in tail for hint in ADMIN_HINTS):
        return "admin"
    if base.endswith("/"):
        return "directory"
    return "file"


def _is_blocked(response, cfg):
    """返回 (是否拦截, 原因)。429 恒定拦截，其余看响应体指纹。"""
    status = response.get("status", 404)
    if status == 429:
        return True, "429"
    body = str(response.get("body", "")).lower()
    for fingerprint in cfg["fingerprints"]:
        if fingerprint and fingerprint.lower() in body:
            return True, f"body:{fingerprint}"
    return False, ""


def _is_slow(response, cfg):
    return response.get("ms", 0) > cfg["slow_ms"]


def _record_hit(state, word, response):
    status = response.get("status", 404)
    if status not in HIT_STATUSES:
        return
    base, params = _split_path(word)
    entry = state["hits"].get(base)
    if entry is None:
        state["hits"][base] = {
            "path": base,
            "status": status,
            "statuses": [status],
            "params": params,
            "category": _categorize(base),
            "count": 1,
        }
        return
    entry["count"] += 1
    if status not in entry["statuses"]:
        entry["statuses"] = sorted(entry["statuses"] + [status])
    entry["params"] = sorted(set(entry["params"]) | set(params))


def _next_batch(words, state, pending, limit, attempted, tried):
    """组下一批：先补重试被拦截的路径，再取字典里的新路径。

    tried 记录本调用已经请求过的下标——待重试路径只在下一次 probe 调用
    里再请求，单次调用内不会重复发请求。
    """
    size = state["concurrency"]
    if limit is not None:
        size = min(size, max(0, limit - attempted))
    work = []
    for idx in sorted(pending):
        if len(work) >= size:
            break
        if idx not in tried:
            work.append(idx)
    idx = state["cursor"]
    while len(work) < size and idx < len(words):
        if idx not in pending and idx not in tried:
            work.append(idx)
        idx += 1
    return work


def _hit_list(state):
    return [copy.deepcopy(entry) for entry in state["hits"].values()]


def probe(site, concurrency=8, limit=None):
    """跑一轮自适应探测。

    concurrency 为本轮起始并发，探测中按目标反馈在 [1, concurrency] 之间
    自动升降；limit 限制本轮最多尝试多少条路径，用于模拟中断后分批续跑，
    缺省表示跑到完成或站点拒绝访问为止。
    """
    cfg = _SITES.get(site)
    if cfg is None:
        raise ValueError("站点未载入")
    if limit is not None and limit <= 0:
        raise ValueError("limit 必须为正整数")
    words = cfg["words"]
    responses = cfg["responses"]
    state = _STATE[site]

    # concurrency 是并发上限：首次探测从该上限起步；后续调用沿用上次自适应
    # 得到的并发（上一轮被拦降下来的节奏不会因重新调用被重置），
    # 调用方传入更高的新上限时只抬高天花板，不直接拉升当前并发。
    ceiling = max(concurrency, MIN_CONCURRENCY)
    state["max_concurrency"] = max(state["max_concurrency"], ceiling)
    if state["concurrency"] < MIN_CONCURRENCY:
        state["concurrency"] = ceiling
    state["concurrency"] = min(state["concurrency"], state["max_concurrency"])
    state["stopped"] = False
    state["ok_streak"] = 0

    pending = set(state["pending"])
    tried = set()         # 本次调用已请求过的下标，保证不重复请求
    attempted = 0
    refused = 0           # 本轮里第一次请求就被拦的路径数
    batch_seq = len(state["blocked_batches"])

    while True:
        if limit is not None and attempted >= limit:
            break
        work = _next_batch(words, state, pending, limit, attempted, tried)
        if not work:
            break

        before = state["concurrency"]
        blocked_paths = []
        reasons = set()
        slow = False
        settled = []
        for idx in work:
            tried.add(idx)
            word = words[idx]
            response = responses.get(word) or {}
            state["requests"] += 1
            state["requested"][word] = state["requested"].get(word, 0) + 1
            attempted += 1

            blocked, reason = _is_blocked(response, cfg)
            if blocked:
                blocked_paths.append(word)
                reasons.add(reason)
                state["blocked_count"] += 1
                if word not in state["blocked_paths"]:
                    state["blocked_paths"].append(word)
                    refused += 1
                state["block_attempts"][word] = state["block_attempts"].get(word, 0) + 1
                if state["block_attempts"][word] >= MAX_BLOCK_RETRIES:
                    # 跨轮重试预算用尽：最终判定为拦截路径，不再重试，也不记成命中。
                    pending.discard(idx)
                    settled.append(word)
                    state["settled_blocked"].append(word)
                else:
                    # 留到后续 probe 轮次重试，本轮不再请求它。
                    pending.add(idx)
                continue

            pending.discard(idx)
            if _is_slow(response, cfg):
                slow = True
            _record_hit(state, word, response)

        # 游标只顶到“已给结论（命中/不存在/最终判定拦截）”的位置；
        # 本调用还没请求过、以及仍待重试的下标都不能算完成。
        while (state["cursor"] < len(words)
               and state["cursor"] not in pending
               and state["cursor"] in tried):
            state["cursor"] += 1

        if blocked_paths:
            # 出现拦截：并发下调一档（8->7->...->1），干净批次计数清零。
            state["concurrency"] = max(MIN_CONCURRENCY, before - 1)
            state["ok_streak"] = 0
            batch_seq += 1
            state["blocked_batches"].append({
                "batch": batch_seq,
                "paths": list(blocked_paths),
                "reasons": sorted(reasons),
                "settled": list(settled),
                "position": state["cursor"],
                "concurrency_before": before,
                "concurrency_after": state["concurrency"],
            })
            # 站点整体拒绝访问的判定：连续的拦截批次里，本批所有路径都是
            # 第一次请求就被拦的新路径（不是个例的跨轮重试），且累计被拒的
            # 新路径数达到阈值——说明目标对从未见过的路径也一律返回拦截页。
            # 此时停止本轮、保留已完成进度，拒绝页不算空结果。
            all_fresh = len(blocked_paths) == len(work) and len(blocked_paths) > 0
            if all_fresh and refused >= REFUSE_STRIKES:
                state["stopped"] = True
                state["pending"] = sorted(pending)
                _save(site)
                break
        elif slow:
            # 没有拦截但响应变慢：同样温和降一档，不把路径标成拦截。
            state["concurrency"] = max(MIN_CONCURRENCY, before - 1)
            state["ok_streak"] = 0
        else:
            # 干净批次攒够后逐步把并发提回上限。
            state["ok_streak"] += 1
            if state["ok_streak"] >= RECOVER_BATCHES:
                state["concurrency"] = min(state["max_concurrency"], before + 1)
                state["ok_streak"] = 0

        state["pending"] = sorted(pending)
        # 每批落盘：进程随时中断，已完成的进度都不会丢。
        _save(site)

    state["pending"] = sorted(pending)
    if state["cursor"] >= len(words) and not pending:
        state["stopped"] = False
    _save(site)
    return _snapshot(site)


def _snapshot(site):
    state = _STATE[site]
    total = state["total"]
    return {
        "site": site,
        "hits": _hit_list(state),
        "blocked": state["blocked_count"],
        "concurrency": state["concurrency"],
        "resume": state["cursor"],
        "stopped": state["stopped"],
        "blocked_paths": list(state["blocked_paths"]),
        "settled_blocked": list(state["settled_blocked"]),
        "blocked_batches": copy.deepcopy(state["blocked_batches"]),
        "requests": state["requests"],
        "progress": {
            "done": state["cursor"],
            "total": total,
            "pending": list(state["pending"]),
            "percent": round(100.0 * state["cursor"] / total, 1) if total else 0.0,
        },
    }


def report(site):
    """按站点汇总进度：命中分类、拦截批次、当前并发与续跑位置。"""
    state = _STATE.get(site)
    if state is None:
        raise ValueError("站点未扫过")
    groups = {category: [] for category in CATEGORIES}
    for entry in state["hits"].values():
        groups[entry["category"]].append(copy.deepcopy(entry))
    total = state["total"]
    return {
        "site": site,
        "hits": len(state["hits"]),
        "blocked": state["blocked_count"],
        "concurrency": state["concurrency"],
        "resume": state["cursor"],
        "stopped": state["stopped"],
        "groups": groups,
        "group_counts": {category: len(groups[category]) for category in CATEGORIES},
        # 被拦截的路径与批次单独标注，不与命中混在一起。
        "blocked_paths": list(state["blocked_paths"]),
        "settled_blocked": list(state["settled_blocked"]),
        "blocked_batches": copy.deepcopy(state["blocked_batches"]),
        "requests": state["requests"],
        "progress": {
            "done": state["cursor"],
            "total": total,
            "pending": list(state["pending"]),
            "percent": round(100.0 * state["cursor"] / total, 1) if total else 0.0,
        },
    }
