"""基于稳定键的列表页增量比对（纯标准库）。"""

import hashlib
import json
import unicodedata

# 稳定键候选字段：先详情链接，再业务编号
_DETAIL_LINK_FIELDS = ("detail_url", "url", "link", "href")
_BIZ_ID_FIELDS = ("id", "biz_id", "item_id", "code", "no", "number", "sn")

# 状态字段：参与下架判断，但不参与内容指纹
_STATUS_FIELDS = ("status", "state")
_ONLINE_FLAG_FIELDS = (
    "is_online",
    "online",
    "is_active",
    "active",
    "enabled",
    "is_enabled",
)
_OFFLINE_VALUES = {
    "offline",
    "delisted",
    "inactive",
    "disabled",
    "removed",
    "下架",
    "已下架",
    "下线",
    "已下线",
    "停用",
}
_TRACKING_PARAMS = frozenset(
    ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm")
)


def normalize_text(value):
    """规范化文本：NFKC 归一、去空白与标点、忽略大小写。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    chars = [
        ch
        for ch in text
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    ]
    return "".join(chars).casefold()


def _clean_url(value):
    """规范化详情链接：去 fragment、跟踪参数和结尾斜杠。"""
    url = str(value).strip().split("#", 1)[0]
    if "?" in url:
        base, query = url.split("?", 1)
        kept = [
            pair
            for pair in query.split("&")
            if pair and pair.split("=", 1)[0].lower() not in _TRACKING_PARAMS
        ]
        url = base + ("?" + "&".join(kept) if kept else "")
    return url.rstrip("/")


def stable_key(item):
    """计算条目的稳定键：详情链接 > 业务编号 > 规范化标题 > 内容兜底。"""
    for field in _DETAIL_LINK_FIELDS:
        value = item.get(field)
        if value:
            return "url:" + _clean_url(value)
    for field in _BIZ_ID_FIELDS:
        value = item.get(field)
        if value not in (None, ""):
            return "id:{}:{}".format(field, str(value).strip())
    title_key = normalize_text(item.get("title", ""))
    if title_key:
        return "title:" + title_key
    return "raw:" + fingerprint(item)


def is_offline(item):
    """判断条目是否处于下架/下线状态。"""
    for field in _STATUS_FIELDS:
        value = item.get(field)
        if isinstance(value, str):
            if value.strip().casefold() in _OFFLINE_VALUES:
                return True
        elif value in _OFFLINE_VALUES:
            return True
    for field in _ONLINE_FLAG_FIELDS:
        if field in item and item[field] is False:
            return True
    return False


def _normalize_payload(item):
    payload = {}
    excluded = set(_STATUS_FIELDS) | set(_ONLINE_FLAG_FIELDS)
    for key, value in item.items():
        if key in excluded:
            continue
        if key in _DETAIL_LINK_FIELDS:
            payload[key] = _clean_url(value)
        elif isinstance(value, str):
            payload[key] = normalize_text(value)
        else:
            payload[key] = value
    return payload


def fingerprint(item):
    """计算内容指纹：规范化后做稳定 JSON 序列化并取 sha256。"""
    canonical = json.dumps(
        _normalize_payload(item),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def merge_pages(pages):
    """合并多页结果，按稳定键去重并保留首次出现的条目与顺序。"""
    merged = {}
    for page in pages:
        for item in page:
            key = stable_key(item)
            if key not in merged:
                merged[key] = item
    return list(merged.values())


def _index_by_key(items):
    indexed = {}
    for item in items:
        indexed.setdefault(stable_key(item), item)
    return indexed


def diff(old, new):
    """按稳定键做增量比对，输出新增、更新、删除、下架和顺序变化。"""
    old_map = _index_by_key(old)
    new_map = _index_by_key(new)
    old_keys = list(old_map)
    new_keys = list(new_map)
    old_index = {key: index for index, key in enumerate(old_keys)}
    new_index = {key: index for index, key in enumerate(new_keys)}

    result = {
        "added": [new_map[key] for key in new_keys if key not in old_map],
        "removed": [old_map[key] for key in old_keys if key not in new_map],
    }

    updated = [
        new_map[key]
        for key in new_keys
        if key in old_map
        and fingerprint(old_map[key]) != fingerprint(new_map[key])
    ]
    if updated:
        result["updated"] = updated

    delisted = [
        new_map[key]
        for key in new_keys
        if key in old_map
        and is_offline(new_map[key])
        and not is_offline(old_map[key])
    ]
    if delisted:
        result["delisted"] = delisted

    # 顺序变化只看共同键的相对次序，新增/删除导致的位移不算重排
    common_new_order = [key for key in new_keys if key in old_map]
    common_old_order = sorted(common_new_order, key=old_index.get)
    if common_new_order != common_old_order:
        reordered = []
        for new_rank, key in enumerate(common_new_order):
            old_rank = common_old_order.index(key)
            if old_rank != new_rank:
                moved = new_map[key]
                reordered.append(
                    {
                        "key": key,
                        "title": moved.get(
                            "title", old_map[key].get("title", "")
                        ),
                        "old_index": old_index[key],
                        "new_index": new_index[key],
                    }
                )
        if reordered:
            result["reordered"] = reordered

    return result
