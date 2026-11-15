"""采集页面版本追踪：带序号、指纹、变更日志和订阅。

口径：
- 每次保存计算内容指纹（sha256），内容未变不新增版本。
- 每个版本有页内序号 version_no（1 起）和全局变更序号 seq。
- 首次收录不算变更；内容变化或回滚才写入变更日志。
- 订阅方按全局序号增量拉取，游标持久，断线后续拉。
"""

import hashlib
import itertools
import time
from urllib.parse import urlparse

_pages = {}    # page -> {"content", "digest", "version"}
_versions = {}  # page -> [版本记录, ...]，回滚也保留
_log = []      # 变更日志，条目带全局序号 seq
_subs = {}     # 订阅 id -> {"site", "cursor"}
_seq = itertools.count(1)
_sub_seq = itertools.count(1)


def _site(page):
    return urlparse(page).netloc


def _fingerprint(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def save(page, content):
    """保存页面。内容未变不新增版本；返回被取代的旧版本/指纹（无则 None）。"""
    digest = _fingerprint(content)
    now = time.time()
    current = _pages.get(page)
    if current is not None and current["digest"] == digest:
        return {"page": page, "version": None, "fingerprint": None,
                "created": False, "seq": None,
                "version_no": current["version"], "digest": digest}
    previous_version = current["version"] if current else None
    previous_digest = current["digest"] if current else None
    version_no = (previous_version or 0) + 1
    _versions.setdefault(page, []).append(
        {"version": version_no, "fingerprint": digest,
         "content": content, "saved_at": now})
    _pages[page] = {"content": content, "digest": digest, "version": version_no}
    seq = None
    if current is not None:  # 首次收录不算变更
        seq = next(_seq)
        _log.append({"seq": seq, "page": page, "site": _site(page),
                     "version": version_no, "fingerprint": digest,
                     "saved_at": now, "kind": "modified"})
    return {"page": page, "version": previous_version,
            "fingerprint": previous_digest, "created": True, "seq": seq,
            "version_no": version_no, "digest": digest}


def changes(since, site=None, start=None, end=None):
    """按序号续拉变更，可按站点和时间过滤；cursor 是下次续点。"""
    entries = [dict(e) for e in _log
               if e["seq"] > since
               and (site is None or e["site"] == site)
               and (start is None or e["saved_at"] >= start)
               and (end is None or e["saved_at"] <= end)]
    return {"since": since, "changes": entries,
            "cursor": _log[-1]["seq"] if _log else None}


def versions(page):
    """页面的全部历史版本（含内容），回滚不删记录。"""
    return {"page": page,
            "versions": [dict(r) for r in _versions.get(page, [])]}


def rollback(page, version):
    """回滚到指定历史版本：生成新版本并记一条 rollback 变更，历史保留。"""
    for record in _versions.get(page, []):
        if record["version"] == version:
            content = record["content"]
            break
    else:
        raise KeyError("unknown version %r for %r" % (version, page))
    receipt = save(page, content)
    if receipt["seq"] is not None:
        _log[-1]["kind"] = "rollback"
    receipt["rolled_back_to"] = version
    return receipt


def subscribe(site=None):
    """注册订阅，游标从当前日志末端开始；返回订阅 id。"""
    sub_id = "sub-%d" % next(_sub_seq)
    _subs[sub_id] = {"site": site,
                     "cursor": _log[-1]["seq"] if _log else 0}
    return {"subscription": sub_id, "site": site,
            "cursor": _subs[sub_id]["cursor"]}


def pull(subscription, since=None):
    """按订阅游标拉增量并推进游标；断线后用同一订阅 id 续拉。

    传 since 可回放历史，不推进游标。
    """
    sub = _subs[subscription]
    cursor = sub["cursor"] if since is None else since
    result = changes(cursor, site=sub["site"])
    if since is None and result["cursor"] is not None:
        sub["cursor"] = result["cursor"]
    return {"subscription": subscription, "changes": result["changes"],
            "cursor": sub["cursor"]}


def reset():
    """清空全部状态（测试和演示用）。"""
    global _seq, _sub_seq
    _pages.clear()
    _versions.clear()
    _log.clear()
    _subs.clear()
    _seq = itertools.count(1)
    _sub_seq = itertools.count(1)
