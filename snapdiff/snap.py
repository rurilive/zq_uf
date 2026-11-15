"""页面快照与变更检测：按内容块取指纹，变更定位到段落。"""
from __future__ import annotations

import difflib

from .blocks import (
    content_hash,
    extract_blocks,
    is_counter_noise,
    normalize_text,
    snippet,
)

__all__ = ["snapshot", "diff"]


def snapshot(page, store=None, site=None):
    """对页面取快照。

    只保留正文块：脚本、样式、广告位和计数器在解析阶段就被剔除，
    因此它们的变化不会改变快照指纹。传入 ``store`` 时按站点入库并分配版本号。
    """
    page = page or {}
    blocks = extract_blocks(page.get("html"), ignore=page.get("ignore"))
    record = {
        "hash": content_hash(blocks),
        "blocks": blocks,
        "version": 1,
        "site": site or page.get("site") or page.get("url"),
        "url": page.get("url"),
        "title": page.get("title"),
        "timestamp": page.get("timestamp"),
    }
    if store is not None:
        if record["site"] is None:
            raise ValueError("snapshot needs a site/url when saving to a store")
        return store.add(record["site"], record)
    return record


def _block_list(snap):
    if snap is None:
        return []
    if not isinstance(snap, dict):
        raise TypeError("snapshot must be a dict, got {}".format(type(snap).__name__))
    blocks = snap.get("blocks")
    if blocks is None:
        return extract_blocks(snap.get("html"))
    return blocks


def _key(block):
    fingerprint = block.get("fingerprint")
    if fingerprint:
        return fingerprint
    return normalize_text(block.get("text"))


def _describe(change):
    before = change.get("before_summary") or ""
    after = change.get("after_summary") or ""
    label = "第{}段".format(change["new_index"] + 1 if change.get("new_index") is not None
                            else change["old_index"] + 1)
    if change["type"] == "modified":
        return "{}修改：「{}」→「{}」".format(label, before, after)
    if change["type"] == "added":
        return "{}新增：「{}」".format(label, after)
    return "{}删除：「{}」".format(label, before)


def _make_change(kind, old_block=None, new_block=None):
    old_index = old_block["index"] if old_block else None
    new_index = new_block["index"] if new_block else None
    change = {
        "type": kind,
        "old_index": old_index,
        "new_index": new_index,
        "paragraph": (new_index if new_index is not None else old_index) + 1,
        "tag": (new_block or old_block or {}).get("tag"),
        "before": normalize_text((old_block or {}).get("text")),
        "after": normalize_text((new_block or {}).get("text")),
    }
    change["before_summary"] = snippet(change["before"])
    change["after_summary"] = snippet(change["after"])
    change["summary"] = _describe(change)
    return change


def diff_blocks(old_blocks, new_blocks):
    """按段落稳定指纹做序列比对，返回逐个变化（修改 / 新增 / 删除）。

    指纹已忽略脚本/广告（提取阶段剔除）和计数器数字；若调用方传入的块
    没有指纹，这里再按计数器规则兜底过滤一次。
    """
    old_keys = [_key(block) for block in old_blocks]
    new_keys = [_key(block) for block in new_blocks]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    changes = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                old_block, new_block = old_blocks[i1 + offset], new_blocks[j1 + offset]
                if is_counter_noise(old_block.get("text"), new_block.get("text")):
                    continue
                changes.append(_make_change("modified", old_block, new_block))
            for index in range(i1 + paired, i2):
                changes.append(_make_change("removed", old_block=old_blocks[index]))
            for index in range(j1 + paired, j2):
                changes.append(_make_change("added", new_block=new_blocks[index]))
        elif tag == "delete":
            for index in range(i1, i2):
                changes.append(_make_change("removed", old_block=old_blocks[index]))
        elif tag == "insert":
            for index in range(j1, j2):
                changes.append(_make_change("added", new_block=new_blocks[index]))
    return changes


def diff(old, new, store=None):
    """比较两份快照，定位变化的段落。

    返回 ``changed``（正文是否变化）、``changes``（逐段变化及前后摘要）、
    ``version``（新版版本号）、``alert``（是否应发告警）和 ``notify``
    （站点观察期已满且通知已开启时才会真正推送）。
    广告、脚本、样式和计数器的变化不产生段落变化，因此 ``changed`` 为假。
    """
    new = new or {}
    old_blocks = _block_list(old)
    new_blocks = _block_list(new)
    changes = diff_blocks(old_blocks, new_blocks)
    changed = bool(changes)
    old_hash = (old or {}).get("hash") or content_hash(old_blocks)
    new_hash = new.get("hash") or content_hash(new_blocks)

    site = new.get("site") or (old or {}).get("site")
    alerts_enabled = True
    notify = False
    observation_done = False
    if store is not None:
        if site is not None:
            alerts_enabled = store.alerts_enabled(site)
            notify = bool(changed and store.notifications_active(site))
            observation_done = store.observation_elapsed(site)

    summary = "；".join(change["summary"] for change in changes)
    if not summary:
        summary = "正文无变化"
    elif len(changes) > 1:
        summary = "正文有 {} 处变化：{}".format(len(changes), summary)

    result = {
        "site": site,
        "changed": changed,
        "content_changed": changed,
        "hash_changed": old_hash != new_hash,
        "old_version": (old or {}).get("version"),
        "version": new.get("version", 1),
        "new_version": new.get("version", 1),
        "changes": changes,
        "blocks": changes,
        "paragraphs": [change["paragraph"] for change in changes],
        "summary": summary,
        "alert": bool(changed and alerts_enabled),
        "notify": bool(notify),
        "alerts_enabled": alerts_enabled,
        "false_positive": False,
        "observation_done": observation_done,
    }
    if store is not None and site is not None:
        result["false_positive"] = store.is_false_positive(site, result["version"])
        store.record_result(site, result)
    return result
