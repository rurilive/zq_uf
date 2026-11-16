"""样本入库、相似归组与家族报表。

判重分两层：

* 完整 digest 相同：只保留一份，返回 ``duplicate``。
* digest 不同但内容相似：样本仍会入库，方便逐份对比，但归入同一个相似组，
  组内选一份 medoid 作为代表。相似度按内容指纹（4-gram 指纹集合的 Jaccard）
  计算，阈值可以按家族调整。

入库失败的样本不会抛异常，而是带着原因留档，方便筛选和补处理。
"""

import difflib
import heapq
import zlib

DEFAULT_THRESHOLD = 0.85
_SHINGLE_SIZE = 4
_MAX_SCAN = 200000
_SAMPLE_TARGET = 256
_MAX_DIFF_POSITIONS = 20
_DIFF_BLOCK = 64
_EXACT_DIFF_LIMIT = 4000

_ITEMS = []
_ITEMS_BY_ID = {}
_INDEX_BY_DIGEST = {}
_FAILURES = []
_THRESHOLDS = {}
_GROUP_CACHE = {}
_SIM_CACHE = {}
_NEXT_ID = 1
_DUPLICATES = 0
_FAMILY_STATS = {}


def reset():
    """清空内存中的样本、分组、阈值、失败记录和计数。"""
    global _NEXT_ID, _DUPLICATES
    _ITEMS.clear()
    _ITEMS_BY_ID.clear()
    _INDEX_BY_DIGEST.clear()
    _FAILURES.clear()
    _THRESHOLDS.clear()
    _GROUP_CACHE.clear()
    _SIM_CACHE.clear()
    _FAMILY_STATS.clear()
    _NEXT_ID = 1
    _DUPLICATES = 0


# ---------------------------------------------------------------------------
# 内容指纹与相似度


def _content_bytes(content):
    if isinstance(content, str):
        return content.encode("utf-8", "surrogatepass")
    if isinstance(content, (bytes, bytearray, memoryview)):
        return bytes(content)
    return None


def _extract_content(sample):
    for key in ("content", "data", "bytes"):
        if key in sample:
            data = _content_bytes(sample.get(key))
            if data is not None:
                return data
    return None


def _shingles(data):
    """内容定义采样的 4-gram 指纹集合。

    保留 CRC 值最小的 _SAMPLE_TARGET 个 4-gram 指纹（bottom-k 采样）。保留
    与否只取决于 4-gram 的内容，与它在文件中的位置无关，因此插入或删除字节
    不会让整个指纹集合错位；改一个字节只影响它覆盖的 4 个 4-gram。

    超过 _MAX_SCAN 字节的文件按位置等距抽检 4-gram，样本量有上限。
    """
    if not data:
        return frozenset()
    if len(data) <= _SHINGLE_SIZE:
        return frozenset([zlib.crc32(data)])

    count = len(data) - _SHINGLE_SIZE + 1
    step = max(1, count // _MAX_SCAN)
    seen = {zlib.crc32(data[i:i + _SHINGLE_SIZE])
            for i in range(0, count, step)}
    return frozenset(heapq.nsmallest(_SAMPLE_TARGET, seen))


def _jaccard(left, right):
    if not left and not right:
        return 1.0
    union = len(left | right)
    if not union:
        return 1.0
    return len(left & right) / union


def _item_similarity(left, right):
    """两份样本的内容相似度，0.0 ~ 1.0。

    归组统一用 4-gram 指纹集合的 Jaccard 相似度，代价是常数级；差异明细
    另行计算。
    """
    if left["digest"] == right["digest"]:
        return 1.0
    if left["content"] is None or right["content"] is None:
        return 0.0
    if left["content"] == right["content"]:
        return 1.0
    if left["fingerprint"] is None or right["fingerprint"] is None:
        return 0.0
    return _jaccard(left["fingerprint"], right["fingerprint"])


def _cached_similarity(left, right):
    key = (min(left["id"], right["id"]), max(left["id"], right["id"]))
    if key not in _SIM_CACHE:
        _SIM_CACHE[key] = _item_similarity(left, right)
    return _SIM_CACHE[key]


# ---------------------------------------------------------------------------
# 分组


def _threshold(family):
    return _THRESHOLDS.get(family, DEFAULT_THRESHOLD)


def _find(item_id):
    return _ITEMS_BY_ID.get(item_id)


def _find_item(key):
    """按样本 id 或名称查找。名称重复时返回最早入库的一份。"""
    if isinstance(key, int):
        return _find(key)
    for item in _ITEMS:
        if item["name"] == key:
            return item
    return None


def _group_members(group):
    return [_find(item_id) for item_id in group["member_ids"]]


def _choose_representative(scores):
    """medoid：与组内其他成员相似度总和最高；并列取最早入库者。"""
    return min(scores, key=lambda item_id: (-scores[item_id], item_id))


def _new_group(family, members, threshold, scores=None):
    """按成员重建一个组；未给出得分时按两两相似度现算。"""
    if scores is None:
        scores = {item["id"]: 0.0 for item in members}
        for index, left in enumerate(members):
            for right in members[index + 1:]:
                similarity = _cached_similarity(left, right)
                scores[left["id"]] += similarity
                scores[right["id"]] += similarity
    members = sorted(members, key=lambda item: item["id"])
    group = {
        "id": "{}#{}".format(family, members[0]["id"]),
        "family": family,
        "member_ids": [item["id"] for item in members],
        "representative_id": _choose_representative(scores),
        "threshold": threshold,
        "scores": scores,
    }
    for item in members:
        item["group"] = group["id"]
    return group


def _merge_groups(family, groups, threshold):
    """把同家族的全部组按当前阈值重算为连通分量。"""
    members = [item for item in _ITEMS if item["family"] == family]
    parent = {item["id"]: item["id"] for item in members}

    def find_root(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for index, left in enumerate(members):
        for right in members[index + 1:]:
            if _cached_similarity(left, right) >= threshold:
                left_root, right_root = find_root(left["id"]), find_root(right["id"])
                if left_root != right_root:
                    parent[max(left_root, right_root)] = min(left_root, right_root)

    buckets = {}
    for item in members:
        buckets.setdefault(find_root(item["id"]), []).append(item)
    groups[:] = sorted(
        (_new_group(family, bucket, threshold) for bucket in buckets.values()),
        key=lambda group: group["member_ids"][0],
    )
    return groups


def _regroup_family(family):
    """按当前阈值全量重算一个家族的相似组。"""
    members = [item for item in _ITEMS if item["family"] == family]
    if not members:
        _GROUP_CACHE.pop(family, None)
        return []
    groups = _GROUP_CACHE.setdefault(family, [])
    return _merge_groups(family, groups, _threshold(family))


def _attach_to_family(item):
    """增量归组：只与同家族已有样本比对，合并被触及的组并增量更新得分。

    每个新增样本只与家族内已有样本各比一次，组内相似度得分随合并增量维护，
    因此不必在每次入库时对整组做 O(n^2) 重算；结果与全量重算一致。
    """
    family = item["family"]
    threshold = _threshold(family)
    groups = _GROUP_CACHE.setdefault(family, [])

    touched = []
    similarities = {}
    for other in _ITEMS:
        if other is item or other["family"] != family:
            continue
        similarity = _cached_similarity(item, other)
        # 组内代表得分按全部两两相似度累加，阈值只决定是否同组。
        similarities[other["id"]] = similarity
        if similarity < threshold:
            continue
        group = _group_for_item(other)
        if group is not None and group not in touched:
            touched.append(group)

    if not touched:
        group = _new_group(family, [item], threshold, scores={item["id"]: 0.0})
        groups.append(group)
        groups.sort(key=lambda entry: entry["member_ids"][0])
        return group

    # 把所有可达组并成一份：同一组的成员得分已互含，跨组补交叉项。
    primary = touched[0]
    for other in touched[1:]:
        primary = _absorb_group(primary, other, groups)

    # 新样本的得分只累加组内成员；家族里未并入本组的样本不计入。
    total = 0.0
    for member_id in primary["member_ids"]:
        similarity = similarities[member_id]
        primary["scores"][member_id] += similarity
        total += similarity
    primary["scores"][item["id"]] = total
    primary["member_ids"].append(item["id"])
    primary["member_ids"].sort()
    item["group"] = primary["id"]
    primary["representative_id"] = _choose_representative(primary["scores"])
    return primary


def _absorb_group(target, source, groups):
    """把 source 组并入 target 组，只补两组之间的相似度交叉项。"""
    target_members = _group_members(target)
    source_members = _group_members(source)
    for right in source_members:
        target["scores"][right["id"]] = source["scores"][right["id"]]
    for left in target_members:
        for right in source_members:
            similarity = _cached_similarity(left, right)
            target["scores"][left["id"]] += similarity
            target["scores"][right["id"]] += similarity
    target["member_ids"].extend(source["member_ids"])
    target["member_ids"].sort()
    for member in source_members:
        member["group"] = target["id"]
    groups.remove(source)
    return target


def _group_for_item(item):
    if item is None or item.get("group") is None:
        return None
    for group in _GROUP_CACHE.get(item["family"], []):
        if group["id"] == item["group"]:
            return group
    return None


def _groups_for(families):
    groups = []
    for family in sorted(families):
        groups.extend(_GROUP_CACHE.get(family, []))
    return groups


# ---------------------------------------------------------------------------
# 差异


def _align_regions(left, right):
    """大文件差异区段：rsync 式块对齐，线性时间。

    以 _DIFF_BLOCK 字节为块在右串建索引，左串按块前进；命中就跳过，未命中
    就累积成差异区。区段边界再逐字节收边，因此孤立改动能精确到字节，密集
    改动则给出覆盖这些改动的区段。
    """
    block = _DIFF_BLOCK
    index = {}
    for offset in range(0, max(0, len(right) - block + 1), block):
        index.setdefault(right[offset:offset + block], []).append(offset)

    regions = []
    left_pos = right_pos = 0
    pending = None

    def flush(end_left, end_right):
        nonlocal pending
        if pending is None:
            return
        region = _trim_region(left, pending[0], end_left,
                              right, pending[1], end_right)
        if region[2] or region[3]:
            regions.append(region)
        pending = None

    while left_pos + block <= len(left):
        chunk = left[left_pos:left_pos + block]
        if right_pos + block <= len(right) \
                and right[right_pos:right_pos + block] == chunk:
            flush(left_pos, right_pos)
            left_pos += block
            right_pos += block
            continue
        candidates = index.get(chunk)
        anchor = _closest(candidates, right_pos) if candidates else None
        if anchor is None:
            if pending is None:
                pending = (left_pos, right_pos)
            left_pos += block
            continue
        flush(left_pos, anchor)
        left_pos += block
        right_pos = anchor + block

    if pending is not None:
        flush(len(left), len(right))
    elif left_pos < len(left) or right_pos < len(right):
        region = _trim_region(left, left_pos, len(left),
                              right, right_pos, len(right))
        if region[2] or region[3]:
            regions.append(region)
    return regions


def _closest(candidates, target):
    return min(candidates, key=lambda offset: abs(offset - target))


def _trim_region(left, left_start, left_end, right, right_start, right_end):
    """差异区段两端逐字节收边，孤立改动得到精确偏移。"""
    while left_start < left_end and right_start < right_end \
            and left[left_start] == right[right_start]:
        left_start += 1
        right_start += 1
    while left_start < left_end and right_start < right_end \
            and left[left_end - 1] == right[right_end - 1]:
        left_end -= 1
        right_end -= 1
    return (left_start, right_start, left_end - left_start,
            right_end - right_start)


def _key_differences(representative, variant):
    """组内样本相对代表的差异：大小与关键差异位置。"""
    left = representative["content"]
    right = variant["content"]
    result = {
        "representative": representative["name"],
        "sample": variant["name"],
        "size": variant["size"],
        "representative_size": representative["size"],
        "size_delta": variant["size"] - representative["size"],
        "positions": [],
        "offsets": [],
        "position_count": 0,
        "truncated": False,
        "granularity": "byte",
    }
    if left is None or right is None:
        result["detail"] = "no_content"
        return result

    prefix = 0
    shortest = min(len(left), len(right))
    while prefix < shortest and left[prefix] == right[prefix]:
        prefix += 1

    suffix = 0
    while (suffix < shortest - prefix
           and left[len(left) - 1 - suffix] == right[len(right) - 1 - suffix]):
        suffix += 1

    middle_left = left[prefix:len(left) - suffix]
    middle_right = right[prefix:len(right) - suffix]

    if not middle_left and not middle_right:
        positions = []
    elif len(middle_left) + len(middle_right) <= _EXACT_DIFF_LIMIT:
        matcher = difflib.SequenceMatcher(None, middle_left, middle_right,
                                          autojunk=False)
        positions = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            positions.append({
                "offset": prefix + i1,
                "sample_offset": prefix + j1,
                "length": max(i2 - i1, j2 - j1),
                "kind": tag,
                "granularity": "byte",
                "representative_length": i2 - i1,
                "sample_length": j2 - j1,
            })
    else:
        # difflib 在多编辑点的大文件上会退化到分钟级；rsync 式块对齐是线性的。
        regions = _align_regions(middle_left, middle_right)
        positions = [{
            "offset": prefix + left_start,
            "sample_offset": prefix + right_start,
            "length": max(left_length, right_length),
            "kind": "replace",
            "granularity": "byte",
            "representative_length": left_length,
            "sample_length": right_length,
        } for left_start, right_start, left_length, right_length in regions]

    total = len(positions)
    if total > _MAX_DIFF_POSITIONS:
        positions.sort(key=lambda position: (-position["length"],
                                             position["offset"]))
        positions = positions[:_MAX_DIFF_POSITIONS]
        result["truncated"] = True
        positions.sort(key=lambda position: position["offset"])

    result["positions"] = positions
    result["offsets"] = [position["offset"] for position in positions]
    result["position_count"] = total
    return result


# ---------------------------------------------------------------------------
# 失败留档


def _record_failure(sample, reason, detail):
    family = sample.get("family") if isinstance(sample, dict) else None
    name = sample.get("name") if isinstance(sample, dict) else None
    failure = {
        "name": name,
        "family": family,
        "reason": reason,
        "detail": detail,
        "sample": sample,
    }
    _FAILURES.append(failure)
    return failure


def _failure_result(failure):
    return {
        "status": "failed",
        "name": failure["name"],
        "family": failure["family"],
        "added": False,
        "retained": True,
        "reason": failure["reason"],
        "detail": failure["detail"],
    }


def failures(reason=None):
    """列出失败记录，可按原因筛选。"""
    records = [failure for failure in _FAILURES
               if reason is None or failure["reason"] == reason]
    return [
        {key: value for key, value in failure.items() if key != "sample"}
        for failure in records
    ]


def retry_failed():
    """重新处理留档的失败样本，成功的从失败列表移除。"""
    pending = list(_FAILURES)
    _FAILURES.clear()
    outcome = {"retried": len(pending), "added": 0,
               "duplicate": 0, "failed": 0}
    for failure in pending:
        sample = failure["sample"]
        result = ingest(sample)
        if result["status"] == "failed":
            outcome["failed"] += 1
        elif result["status"] == "duplicate":
            outcome["duplicate"] += 1
        else:
            outcome["added"] += 1
    return outcome


# ---------------------------------------------------------------------------
# 入库


def _resolve_family(sample, content, fingerprint):
    """返回 (family, family_source)。未标注时只在与已有指纹足够相似时继承家族。"""
    provided = sample.get("family")
    if provided:
        return provided, "label"
    if fingerprint is not None:
        best_family = None
        best_score = -1.0
        for item in _ITEMS:
            if item["fingerprint"] is None:
                continue
            score = _jaccard(fingerprint, item["fingerprint"])
            if score > best_score:
                best_score = score
                best_family = item["family"]
        if best_family is not None and best_score >= _threshold(best_family):
            return best_family, "inferred"
    return "unlabeled", "default"


def _bump(family, key):
    stats = _FAMILY_STATS.setdefault(
        family, {"ingested": 0, "added": 0, "duplicates": 0})
    stats[key] += 1


def ingest(sample):
    """写入样本，返回新增、归类、相似组和差异信息。"""
    global _NEXT_ID, _DUPLICATES

    if not isinstance(sample, dict):
        failure = _record_failure(sample, "invalid_sample",
                                  "sample must be a dict")
        return _failure_result(failure)

    name = sample.get("name")
    if not name:
        failure = _record_failure(sample, "missing_name",
                                  "sample needs a name")
        return _failure_result(failure)

    digest = sample.get("digest")
    if not digest:
        failure = _record_failure(sample, "missing_digest",
                                  "sample needs a digest")
        return _failure_result(failure)

    try:
        content = _extract_content(sample)
    except Exception as error:  # pragma: no cover - defensive
        failure = _record_failure(sample, "invalid_content", str(error))
        return _failure_result(failure)

    raw_size = sample.get("size")
    if raw_size is None:
        size = len(content) if content is not None else 0
    else:
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            failure = _record_failure(sample, "invalid_size",
                                      "size must be an integer")
            return _failure_result(failure)
        if size < 0:
            failure = _record_failure(sample, "invalid_size",
                                      "size must be non-negative")
            return _failure_result(failure)

    existing = _INDEX_BY_DIGEST.get(digest)
    if existing is not None:
        # 完整哈希相同：只留一份，不新增。
        if (existing["content"] is not None and content is not None
                and existing["content"] != content):
            # digest 相同但内容不同：哈希冲突或 digest 填错，不能静默丢弃。
            failure = _record_failure(
                sample, "digest_conflict",
                "digest matches {} but content differs".format(existing["name"]))
            return _failure_result(failure)
        _DUPLICATES += 1
        _bump(existing["family"], "ingested")
        _bump(existing["family"], "duplicates")
        enriched = False
        if existing["content"] is None and content is not None:
            existing["content"] = content
            existing["fingerprint"] = _shingles(content)
            if not sample.get("size"):
                existing["size"] = size
            _SIM_CACHE.clear()
            _regroup_family(existing["family"])
            enriched = True
        group = _group_for_item(existing)
        representative = _find(group["representative_id"]) if group else existing
        differences = None
        similarity = 1.0
        if representative is not None and representative is not existing:
            differences = _key_differences(representative, existing)
            similarity = _cached_similarity(representative, existing)
        return {
            "status": "duplicate",
            "name": name,
            "family": existing["family"],
            "family_source": "label",
            "added": False,
            "enriched": enriched,
            "group": existing["group"],
            "representative": representative["name"] if representative else None,
            "similar": bool(group and len(group["member_ids"]) > 1),
            "similarity": similarity,
            "differences": differences,
            "variant_count": len(group["member_ids"]) if group else 1,
        }

    fingerprint = _shingles(content) if content is not None else None
    family, family_source = _resolve_family(sample, content, fingerprint)
    item = {
        "id": _NEXT_ID,
        "name": name,
        "digest": digest,
        "family": family,
        "size": size,
        "content": content,
        "fingerprint": fingerprint,
        "group": None,
    }
    _NEXT_ID += 1
    _ITEMS.append(item)
    _ITEMS_BY_ID[item["id"]] = item
    _INDEX_BY_DIGEST[digest] = item
    _bump(family, "ingested")
    _bump(family, "added")

    group = _attach_to_family(item)
    representative = _find(group["representative_id"]) if group else item
    differences = None
    similarity = 1.0
    if representative is not None and representative is not item:
        differences = _key_differences(representative, item)
        similarity = _cached_similarity(representative, item)
    return {
        "status": "added",
        "name": name,
        "family": family,
        "family_source": family_source,
        "added": True,
        "group": item["group"],
        "representative": representative["name"] if representative else name,
        "similar": bool(group and len(group["member_ids"]) > 1),
        "similarity": similarity,
        "differences": differences,
        "variant_count": len(group["member_ids"]) if group else 1,
    }


# ---------------------------------------------------------------------------
# 阈值


def set_threshold(family, threshold):
    """调整家族相似度阈值，并重算该家族受影响的组。"""
    if not isinstance(threshold, (int, float)) or not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    previous_threshold = _threshold(family)
    previous_groups = list(_GROUP_CACHE.get(family, []))
    previous_signature = {
        group["id"]: tuple(group["member_ids"]) for group in previous_groups
    }

    if threshold == DEFAULT_THRESHOLD:
        _THRESHOLDS.pop(family, None)
    else:
        _THRESHOLDS[family] = float(threshold)

    groups = _regroup_family(family)
    current_signature = {
        group["id"]: tuple(group["member_ids"]) for group in groups
    }
    affected = sorted(
        group_id for group_id in set(previous_signature) | set(current_signature)
        if previous_signature.get(group_id) != current_signature.get(group_id)
    )
    return {
        "family": family,
        "threshold": _threshold(family),
        "previous_threshold": previous_threshold,
        "previous_group_count": len(previous_groups),
        "group_count": len(groups),
        "affected_groups": affected,
        "groups": [_group_summary(group) for group in groups],
    }


def threshold(family):
    """读取家族的当前相似度阈值。"""
    return _threshold(family)


# ---------------------------------------------------------------------------
# 视图与报表


def _sample_summary(item):
    return {
        "id": item["id"],
        "name": item["name"],
        "digest": item["digest"],
        "size": item["size"],
        "family": item["family"],
        "group": item["group"],
    }


def _group_summary(group, with_differences=True):
    members = [member for member in _group_members(group) if member]
    representative = _find(group["representative_id"])
    variants = []
    for member in members:
        if not with_differences:
            break
        if representative is None or member is representative:
            continue
        diff = _key_differences(representative, member)
        diff["group"] = group["id"]
        diff["family"] = group["family"]
        variants.append({
            "sample": _sample_summary(member),
            "similarity": _cached_similarity(representative, member),
            "difference": diff,
        })
    return {
        "id": group["id"],
        "family": group["family"],
        "threshold": group["threshold"],
        "representative": _sample_summary(representative)
        if representative else None,
        "sample_count": len(members),
        "names": [member["name"] for member in members],
        "samples": [_sample_summary(member) for member in members],
        "variant_count": max(0, len(members) - 1),
        "variants": variants,
        "differences": [variant["difference"] for variant in variants],
    }


def family_view(family):
    """按家族查看分组；组内样本可逐份对比。"""
    groups = [_group_summary(group)
              for group in _GROUP_CACHE.get(family, [])]
    samples = [item for item in _ITEMS if item["family"] == family]
    return {
        "family": family,
        "threshold": _threshold(family),
        "samples": [item["name"] for item in samples],
        "sample_count": len(samples),
        "groups": groups,
        "group_count": len(groups),
        "similar_group_count": sum(
            1 for group in groups if group["sample_count"] > 1),
    }


def group_view(family, group_id=None):
    """查看一个（或该家族全部）相似组的完整内容。"""
    if group_id is None:
        return family_view(family)
    for group in _GROUP_CACHE.get(family, []):
        if group["id"] == group_id:
            return _group_summary(group)
    return None


def compare(left, right):
    """逐份对比两份样本：大小、相似度与关键差异位置。"""
    left_item = _find_item(left)
    right_item = _find_item(right)
    if left_item is None or right_item is None:
        raise ValueError("sample not found")
    return {
        "left": _sample_summary(left_item),
        "right": _sample_summary(right_item),
        "similarity": _item_similarity(left_item, right_item),
        "differences": _key_differences(left_item, right_item),
    }


def _empty_stats():
    return {"ingested": 0, "added": 0, "duplicates": 0}


def report(family=None, failure_reason=None, reason=None):
    """汇总新增、归类、相似组、差异与失败情况。

    参数保持可选，``report()`` 的调用方式与之前一致。``family`` 用于只看
    一个家族，``failure_reason``（别名 ``reason``）用于筛选失败原因。
    """
    filter_reason = failure_reason if failure_reason is not None else reason
    families = ([family] if family is not None
                else sorted({item["family"] for item in _ITEMS}))
    items = [item for item in _ITEMS
             if family is None or item["family"] == family]
    groups = _groups_for(families)

    family_counts = {}
    for item in items:
        family_counts[item["family"]] = family_counts.get(item["family"], 0) + 1

    group_counts = {name: 0 for name in families}
    similar_group_counts = {name: 0 for name in families}
    for group in groups:
        group_counts[group["family"]] = group_counts.get(group["family"], 0) + 1
        if len(group["member_ids"]) > 1:
            similar_group_counts[group["family"]] = (
                similar_group_counts.get(group["family"], 0) + 1)

    if family is None:
        stats = {
            "ingested": sum(value["ingested"]
                            for value in _FAMILY_STATS.values()),
            "added": sum(value["added"] for value in _FAMILY_STATS.values()),
            "duplicates": sum(value["duplicates"]
                              for value in _FAMILY_STATS.values()),
        }
    else:
        stats = dict(_FAMILY_STATS.get(family, _empty_stats()))

    after = len(groups)
    before = stats["ingested"]
    summaries = [_group_summary(group) for group in groups]
    differences = [difference for summary in summaries
                   for difference in summary["differences"]]

    failure_records = [
        failure for failure in _FAILURES
        if (family is None or failure["family"] == family)
        and (filter_reason is None or failure["reason"] == filter_reason)
    ]
    failure_counts = {}
    for failure in _FAILURES:
        if family is not None and failure["family"] != family:
            continue
        failure_counts[failure["reason"]] = (
            failure_counts.get(failure["reason"], 0) + 1)

    return {
        "total": len(items),
        "duplicates": stats["duplicates"],
        "families": dict(sorted(family_counts.items())),
        "samples": [item["name"] for item in items],
        "dedupe": {
            "before": before,
            "after": after,
            "stored": len(items),
            "added": stats["added"],
            "exact_duplicates": stats["duplicates"],
            "similar_merged": max(0, len(items) - after),
            "ratio": (after / before) if before else 0.0,
        },
        "groups": summaries,
        "group_counts": dict(sorted(group_counts.items())),
        "similar_group_counts": dict(sorted(similar_group_counts.items())),
        "differences": differences,
        "diff_count": len(differences),
        "failures": [
            {key: value for key, value in failure.items() if key != "sample"}
            for failure in failure_records
        ],
        "failure_counts": dict(sorted(failure_counts.items())),
        "thresholds": {name: _threshold(name) for name in families},
    }
