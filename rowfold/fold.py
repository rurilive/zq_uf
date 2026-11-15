"""采集结果折叠：主键 + 关键字段相似度选主记录，保留来源与取舍原因。

规则：
1. 主键相同的记录直接归为一组，按字段完整度、更新时间、到达顺序选主记录。
2. 主键不同但关键字段高度相似的记录归为一组，超过阈值才合并。
   相似度用重合系数 |交集| / min(|A|, |B|)，字段少的记录不会因为
   “字段少”而被判低分；共享字段数还要达到 min_shared。
3. 每条被合并的记录保留原始行、来源、相似度、共享字段与取舍原因。
4. 被合并的记录仍可按自己的主键或 row_id 单独查询（explain）。
5. 主记录变更、人工拆分写审计日志；合并过程可按记录回放、导出。
6. 可先在单个业务域试运行（dry_run），确认金额不再重复后再推广（promote）。
7. 人工拆分过的记录默认不再被自动合并（frozen），需要时用 unsplit 恢复。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone

META_FIELDS = frozenset({"key", "row_id", "source", "updated_at"})
_EPS = 1e-9


# ---------------------------------------------------------------------------
# 归一化与比较
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def _norm(value):
    """归一化单元格：大小写、首尾空白、全角空格、千分位与整数/小数差异。

    只有符合规范数字写法的字符串才会去掉千分位并统一成同一形式，
    普通文本（电话、地址、编码）保持原样，避免 “1,2” 与 “12” 被误判相同。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        number = float(value)
        return str(int(number)) if number.is_integer() else repr(number)
    text = unicodedata.normalize("NFKC", str(value)).replace("　", " ").strip().lower()
    if _NUMBER.match(text):
        normalized = text.replace(",", "")
        signed = normalized.lstrip("+-")
        if "." in signed:
            whole, frac = signed.split(".", 1)
            frac = frac.rstrip("0")
            normalized = whole + ("." + frac if frac else "")
        return normalized.lstrip("+")
    return text


def _ts_rank(value):
    """更新时间排序键：(是否有值, 时间戳)。无法解析视为缺失。"""
    if value is None or value == "":
        return (0, 0.0)
    try:
        if isinstance(value, datetime):
            return (1, value.timestamp())
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (1, float(value))
        text = str(value).strip().replace("Z", "+00:00")
        return (1, datetime.fromisoformat(text).timestamp())
    except (TypeError, ValueError):
        return (0, 0.0)


def _field_set(row, fields):
    """记录在指定字段上的 (字段, 归一化值) 集合；空值不参与相似度。"""
    return {
        (field, _norm(row.get(field)))
        for field in fields
        if _norm(row.get(field))
    }


def _overlap(row_a, row_b, fields):
    """关键字段相似度（重合系数）。

        score = 取值一致的字段数 / min(两条记录填了的字段数)

    用 min 而不是并集，字段少的记录（先到、字段不全）不会因为字段数量
    少而被判低分；底线由 min_shared 把关：一致字段数不够就不合并。
    返回 (相似度, 一致的 (字段, 值) 集合)。
    """
    left = _field_set(row_a, fields)
    right = _field_set(row_b, fields)
    if not left or not right:
        return 0.0, frozenset()
    shared = left & right
    return len(shared) / min(len(left), len(right)), frozenset(shared)


def _completeness(row, fields):
    return sum(1 for field in fields if _norm(row.get(field)))


def _amount_value(value):
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("¥", "").strip())
    except ValueError:
        return 0.0


def _now_iso(clock):
    moment = clock()
    if isinstance(moment, datetime):
        return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
    return datetime.fromtimestamp(float(moment), timezone.utc).isoformat(
        timespec="seconds"
    )


def _reason(
    *,
    row_id,
    key,
    source,
    master_id,
    master_key,
    basis,
    score,
    shared_fields,
    kept_completeness,
    row_completeness,
    kept_updated_at,
    row_updated_at,
    decision,
):
    return {
        "row_id": row_id,
        "key": key,
        "source": source,
        "master": master_id,
        "master_key": master_key,
        "basis": basis,
        "score": round(float(score), 4),
        "shared_fields": list(shared_fields),
        "kept_completeness": kept_completeness,
        "row_completeness": row_completeness,
        "kept_updated_at": kept_updated_at,
        "row_updated_at": row_updated_at,
        "decision": decision,
    }


# ---------------------------------------------------------------------------
# 折叠会话
# ---------------------------------------------------------------------------

class RowFold:
    """可复用的折叠会话：保存分组、审计日志与待推广的试运行结果。"""

    def __init__(
        self,
        *,
        key_fields=None,
        similarity_fields=None,
        completeness_fields=None,
        threshold=0.8,
        min_shared=2,
        updated_at="updated_at",
        source="source",
        amount="amount",
        clock=None,
    ):
        if not 0.0 < float(threshold) <= 1.0:
            raise ValueError("threshold 必须在 (0, 1] 之间")
        if int(min_shared) < 1:
            raise ValueError("min_shared 至少为 1")
        self.key_fields = tuple(key_fields) if key_fields else None
        self.similarity_fields = tuple(similarity_fields) if similarity_fields else None
        self.completeness_fields = tuple(completeness_fields) if completeness_fields else None
        self._explicit_similarity = similarity_fields is not None
        self._explicit_min_shared = min_shared != 2
        self.threshold = float(threshold)
        self.min_shared = int(min_shared)
        self.updated_at = updated_at
        self.source = source
        self.amount = amount

        self.groups = OrderedDict()
        self.audit_log = []
        self.frozen = set()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._pending = None
        self._gid = 0
        self._audit_seq = 0

    # -- 配置与内部工具 ---------------------------------------------------

    def _config(self, identity):
        """确定本次折叠实际生效的相似度参数。

        关键字段数不足以满足 min_shared 时，如果参数是用户显式给的，
        说明配置矛盾，直接报错；如果是按数据推导出来的默认值，则关闭
        跨主键合并（只做主键去重），并在配置里写明原因，绝不静默合并。
        """
        explicit = self._explicit_similarity or self._explicit_min_shared
        enough = len(identity) >= self.min_shared
        if not enough and explicit:
            raise ValueError(
                f"关键字段只有 {len(identity)} 个"
                f"（{', '.join(identity) or '无'}），少于 min_shared="
                f"{self.min_shared}，跨主键合并无法生效；"
                f"请缩小 min_shared 或增加 similarity_fields"
            )
        enabled = enough
        return {
            "threshold": self.threshold,
            "min_shared": self.min_shared,
            "effective_min_shared": self.min_shared if enabled else None,
            "cross_key_merge_enabled": enabled,
            "cross_key_merge_disabled_reason": None
            if enabled
            else (
                f"关键字段只有 {len(identity)} 个，少于 min_shared="
                f"{self.min_shared}；本次只按主键去重，未做跨主键合并"
            ),
            "key_fields": list(self.key_fields) if self.key_fields else None,
            "similarity_fields": list(identity),
            "completeness_fields": (
                list(self.completeness_fields) if self.completeness_fields else None
            ),
            "updated_at_field": self.updated_at,
            "source_field": self.source,
            "amount_field": self.amount,
        }

    def _new_gid(self):
        self._gid += 1
        return f"g{self._gid}"

    def _audit(self, entry):
        self._audit_seq += 1
        entry = dict(entry)
        entry["seq"] = self._audit_seq
        entry.setdefault("at", _now_iso(self._clock))
        self.audit_log.append(entry)
        return entry

    def _lookup(self, key):
        """找到记录所在分组。

        人工拆分后可能出现两个分组共用同一主键（同主键未必是重复记录），
        所以先按 row_id 精确匹配，再退回主键匹配。主键可能是整数等非字符串
        类型，统一按字符串比较。
        """
        wanted = str(key)
        for group in self.groups.values():
            if wanted == str(group["master_id"]):
                return group
        for group in self.groups.values():
            if wanted in group["members"]:
                return group
        for group in self.groups.values():
            if wanted == str(group["master_key"]):
                return group
        for group in self.groups.values():
            for member in group["members"].values():
                if wanted == str(member["key"]):
                    return group
        return None

    def _groups_for_keys(self, keys, domain=None):
        found = []
        for group in self.groups.values():
            if domain is not None and group["domain"] != domain:
                continue
            group_keys = set(group["keys"])
            group_keys.add(group["master_key"])
            group_keys.update(member["key"] for member in group["members"].values())
            if group_keys & set(keys):
                found.append(group)
        return found

    @staticmethod
    def _group_view(group):
        return {
            "group": group["id"],
            "domain": group["domain"],
            "fold": group["fold"],
            "master_id": group["master_id"],
            "master_key": group["master_key"],
            "master": dict(group["master"]),
            "keys": list(group["keys"]),
            "members": [
                {
                    "row_id": member["row_id"],
                    "key": member["key"],
                    "source": member["source"],
                    "row": dict(member["row"]),
                    "reason": dict(member["reason"]),
                }
                for member in group["members"].values()
            ],
            "events": [dict(event) for event in group["log"]],
            "created_at": group["created_at"],
            "updated_at": group["updated_at"],
        }

    @staticmethod
    def _path(start, target, adjacency):
        """分组内的最短关联链路，用于解释传递合并。"""
        if start == target:
            return [start]
        queue = [[start]]
        seen = {start}
        while queue:
            path = queue.pop(0)
            for nxt in sorted(adjacency.get(path[-1], ())):
                if nxt == target:
                    return path + [nxt]
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(path + [nxt])
        return [start, target]

    def _same_key_why(self, master, item):
        if item["completeness"] != master["completeness"]:
            return (
                f"主键同为 {master['key']}；保留 {master['row_id']}"
                f"（完整度 {master['completeness']} > {item['completeness']}）"
            )
        if item["ts"] != master["ts"]:
            newer = "更新" if master["ts"] > item["ts"] else "更旧"
            return (
                f"主键同为 {master['key']}；完整度相同"
                f"（{master['completeness']}），保留更新时间{newer}的 "
                f"{master['row_id']}"
            )
        return (
            f"主键同为 {master['key']}；完整度与更新时间相同，"
            f"保留先到的 {master['row_id']}"
        )

    # -- 构建（纯计算，不修改会话） ---------------------------------------

    def _prepare(self, rows):
        prepared = []
        key_counts = {}
        for row in rows:
            if isinstance(row, dict) and "key" in row:
                key_counts[row["key"]] = key_counts.get(row["key"], 0) + 1
        ordinals = {}
        used_ids = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise TypeError(f"第 {index} 条记录不是 dict")
            if "key" not in row:
                raise ValueError(f"第 {index} 条记录缺少主键字段 key")
            key = row["key"]
            if row.get("row_id"):
                row_id = str(row["row_id"])
            elif key_counts.get(key, 0) == 1:
                # 主键唯一时直接用主键，行序变化不影响拆分与回看。
                row_id = str(key)
            else:
                ordinals[key] = ordinals.get(key, -1) + 1
                row_id = f"{key}#{ordinals[key]}"
            base, suffix = row_id, 1
            while row_id in used_ids:
                suffix += 1
                row_id = f"{base}~{suffix}"
            used_ids.add(row_id)
            prepared.append(
                {
                    "index": index,
                    "row_id": row_id,
                    "key": key,
                    "row": dict(row),
                    "source": row.get(self.source),
                    "ts": _ts_rank(row.get(self.updated_at)),
                }
            )
        return prepared

    def _build(self, rows, domain):
        prepared = self._prepare(rows)

        all_fields = set()
        for item in prepared:
            all_fields.update(item["row"])
        data_fields = tuple(sorted(all_fields - META_FIELDS))
        identity = self.key_fields or tuple(f for f in data_fields if f != self.amount)
        similarity = self.similarity_fields or identity
        completeness = self.completeness_fields or data_fields
        config = self._config(similarity)
        effective_min = config["effective_min_shared"]
        for item in prepared:
            item["completeness"] = _completeness(item["row"], completeness)

        free = [item for item in prepared if item["row_id"] not in self.frozen]
        frozen = [item for item in prepared if item["row_id"] in self.frozen]
        position = {item["index"]: offset for offset, item in enumerate(free)}

        # 并查集（只连未冻结的记录）。
        parent = list(range(len(free)))
        adjacency = {offset: set() for offset in range(len(free))}
        pairs = {}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        for i in range(len(free)):
            for j in range(i + 1, len(free)):
                score, shared = _overlap(
                    free[i]["row"], free[j]["row"], similarity
                )
                pairs[(i, j)] = (score, shared)
                same_key = free[i]["key"] == free[j]["key"]
                similar = (
                    effective_min is not None
                    and score >= self.threshold
                    and len(shared) >= effective_min
                )
                if same_key or similar:
                    union(i, j)
                    adjacency[i].add(j)
                    adjacency[j].add(i)

        clusters = OrderedDict()
        for offset in range(len(free)):
            clusters.setdefault(find(offset), []).append(free[offset])
        ordered = sorted(clusters.values(), key=lambda g: g[0]["index"])
        ordered.extend([item] for item in frozen)

        now = _now_iso(self._clock)
        previous = {}
        for group in self.groups.values():
            if group["domain"] != domain:
                continue
            previous.setdefault(group["master_key"], group["master_id"])
            for member in group["members"].values():
                previous.setdefault(member["key"], group["master_id"])

        new_groups = OrderedDict()
        audit_entries = []
        masters = []
        merged_out = []
        reasons = []
        kept_separate = []
        amount_before = sum(
            _amount_value(row.get(self.amount)) for row in rows
        )
        amount_after = 0.0

        for cluster in ordered:
            keys = list(OrderedDict.fromkeys(item["key"] for item in cluster))
            existing = self._groups_for_keys(keys, domain)
            # 重新折叠时沿用原分组 id；一个旧分组被拆成多组时，
            # 只有第一组沿用，其余组重新分配，避免 id 冲突覆盖数据。
            gid = existing[0]["id"] if existing else self._new_gid()
            while gid in new_groups:
                gid = self._new_gid()
            prior_log = []
            seen_events = set()
            for group in existing:
                for event in group["log"]:
                    stamp = json.dumps(event, sort_keys=True, default=str)
                    if stamp not in seen_events:
                        seen_events.add(stamp)
                        prior_log.append(dict(event))
            prior_log.sort(key=lambda event: event.get("step", 0))
            fold_no = 1 + max(
                (event.get("fold", 0) for event in prior_log), default=0
            )
            created_at = min(
                (group["created_at"] for group in existing), default=now
            )

            master = max(
                cluster,
                key=lambda item: (item["completeness"], item["ts"], -item["index"]),
            )
            earliest = min(cluster, key=lambda item: item["index"])
            events = []

            def log(action, **fields):
                event = {"fold": fold_no, "action": action, "at": now, **fields}
                events.append(event)
                return event

            log(
                "select_master",
                master=master["row_id"],
                key=master["key"],
                completeness=master["completeness"],
                updated_at=master["row"].get(self.updated_at),
                why="按字段完整度、更新时间、到达顺序选主记录",
            )

            if master["row_id"] in self.frozen:
                log(
                    "keep_separate",
                    row=master["row_id"],
                    key=master["key"],
                    why="人工拆分过的记录不再自动合并；可用 unsplit 恢复",
                )
                kept_separate.append(
                    {
                        "row_id": master["row_id"],
                        "key": master["key"],
                        "why": "人工拆分过的记录保持独立，未参与自动合并",
                    }
                )

            if earliest["row_id"] != master["row_id"] and not prior_log:
                why = (
                    f"先到的 {earliest['row_id']} 完整度 "
                    f"{earliest['completeness']}，后到的 {master['row_id']} "
                    f"完整度 {master['completeness']}；保留完整的一条"
                )
                log(
                    "master_change",
                    key=master["key"],
                    previous=earliest["row_id"],
                    current=master["row_id"],
                    phase="initial",
                    why=why,
                )
                audit_entries.append(
                    {
                        "action": "master_change",
                        "phase": "initial",
                        "domain": domain,
                        "group": gid,
                        "key": master["key"],
                        "previous": earliest["row_id"],
                        "current": master["row_id"],
                        "why": why,
                    }
                )

            for key in keys:
                old = previous.get(key)
                if old and old != master["row_id"]:
                    why = (
                        f"重新折叠后 {key} 的主记录由 {old} 变更为 "
                        f"{master['row_id']}（完整度 {master['completeness']}）"
                    )
                    log(
                        "master_change",
                        key=key,
                        previous=old,
                        current=master["row_id"],
                        phase="refold",
                        why=why,
                    )
                    audit_entries.append(
                        {
                            "action": "master_change",
                            "phase": "refold",
                            "domain": domain,
                            "group": gid,
                            "key": key,
                            "previous": old,
                            "current": master["row_id"],
                            "why": why,
                        }
                    )

            master_row = dict(master["row"])
            fillable = tuple(f for f in data_fields if f != self.updated_at)
            members = OrderedDict()

            absorbed = sorted(
                (item for item in cluster if item is not master),
                key=lambda item: (-item["completeness"], item["index"]),
            )
            for item in absorbed:
                left, right = position.get(master["index"]), position.get(item["index"])
                if left is not None and right is not None:
                    score, shared = pairs.get(
                        (min(left, right), max(left, right)),
                        _overlap(master["row"], item["row"], similarity),
                    )
                else:
                    score, shared = _overlap(
                        master["row"], item["row"], similarity
                    )
                shared_fields = sorted(field for field, _ in shared)

                if item["key"] == master["key"]:
                    basis = "same_key"
                    why = self._same_key_why(master, item)
                elif (
                    effective_min is not None
                    and score >= self.threshold
                    and len(shared) >= effective_min
                ):
                    basis = "similarity"
                    why = (
                        f"主键不同（{item['key']} / {master['key']}）；"
                        f"关键字段重合 {len(shared)} 个，相似度 "
                        f"{score:.2f} ≥ 阈值 {self.threshold:.2f}"
                    )
                else:
                    path = self._path(
                        position[master["index"]], position[item["index"]], adjacency
                    )
                    via = free[path[-2]]["row_id"] if len(path) >= 2 else "未知"
                    basis = "transitive"
                    why = (
                        f"主键不同（{item['key']} / {master['key']}）；"
                        f"经 {via} 传递关联，与主记录直接相似度 {score:.2f}，"
                        f"链路 {' -> '.join(free[i]['row_id'] for i in path)}"
                    )

                reason = _reason(
                    row_id=item["row_id"],
                    key=item["key"],
                    source=item["source"],
                    master_id=master["row_id"],
                    master_key=master["key"],
                    basis=basis,
                    score=score,
                    shared_fields=shared_fields,
                    kept_completeness=master["completeness"],
                    row_completeness=item["completeness"],
                    kept_updated_at=master["row"].get(self.updated_at),
                    row_updated_at=item["row"].get(self.updated_at),
                    decision=why,
                )
                members[item["row_id"]] = {
                    "row_id": item["row_id"],
                    "key": item["key"],
                    "source": item["source"],
                    "row": dict(item["row"]),
                    "reason": reason,
                }
                reasons.append(reason)
                log(
                    "merge",
                    row=item["row_id"],
                    key=item["key"],
                    basis=basis,
                    score=reason["score"],
                    why=why,
                )

                for field in fillable:
                    if field == self.source or _norm(master_row.get(field)):
                        continue
                    value = item["row"].get(field)
                    if _norm(value):
                        master_row[field] = value
                        log(
                            "fill_field",
                            row=item["row_id"],
                            field=field,
                            value=value,
                            why=f"主记录缺少 {field}，用 {item['row_id']} 补齐",
                        )

            group = {
                "id": gid,
                "domain": domain,
                "master_id": master["row_id"],
                "master_key": master["key"],
                "master": master_row,
                "keys": keys,
                "members": members,
                "log": prior_log + events,
                "created_at": created_at,
                "updated_at": now,
                "fold": fold_no,
            }
            new_groups[gid] = group
            amount_after += _amount_value(master_row.get(self.amount))

            masters.append(
                {
                    "row_id": master["row_id"],
                    "key": master["key"],
                    "source": master["source"],
                    "row": dict(master_row),
                    "completeness": _completeness(master_row, completeness),
                    "merged_count": len(members),
                    "fields_filled": [
                        event["field"]
                        for event in events
                        if event["action"] == "fill_field"
                    ],
                    "frozen": master["row_id"] in self.frozen,
                }
            )
            for member in members.values():
                merged_out.append(member)

        result = {
            "domain": domain,
            "folded": len(merged_out),
            "rows": [dict(item["row"]) for item in masters],
            "masters": masters,
            "merged": [
                {
                    "row_id": member["row_id"],
                    "key": member["key"],
                    "source": member["source"],
                    "row": dict(member["row"]),
                    "reason": dict(member["reason"]),
                }
                for member in merged_out
            ],
            "reasons": reasons,
            "kept_separate": kept_separate,
            "audit": audit_entries,
            "config": config,
            "amounts": {
                "field": self.amount,
                "before": round(amount_before, 6),
                "after": round(amount_after, 6),
                "duplicates_removed": round(amount_before - amount_after, 6),
            },
        }
        result["checks"] = {
            "amount_not_increased": amount_after <= amount_before + _EPS,
            "all_merged_have_reason": all(
                reason.get("decision") for reason in reasons
            ),
            "one_master_per_group": len(new_groups) == len(masters),
            "masters_traceable": all(
                item["row_id"] in {entry["row_id"] for entry in prepared}
                for item in masters
            ),
        }
        result["safe_to_promote"] = all(result["checks"].values())
        return {
            "result": result,
            "groups": new_groups,
            "audit": audit_entries,
        }

    # -- 对外接口 ---------------------------------------------------------

    def fold(self, rows, *, domain=None, dry_run=False):
        """折叠一批记录。dry_run 只计算不落库，需再调用 promote() 推广。"""
        built = self._build(list(rows), domain)
        result = built["result"]
        result["dry_run"] = bool(dry_run)
        if dry_run:
            self._pending = {"domain": domain, **built}
        else:
            self._commit(built)
            self._pending = None
        return result

    def dry_run(self, rows, *, domain=None):
        return self.fold(rows, domain=domain, dry_run=True)

    def promote(self, domain=None):
        """把最近一次试运行的结果提交为正式结果。"""
        if not self._pending:
            raise RuntimeError("没有待推广的试运行结果")
        if domain is not None and self._pending["domain"] != domain:
            raise ValueError(
                f"待推广的业务域是 {self._pending['domain']!r}，不是 {domain!r}"
            )
        pending = self._pending
        self._commit(pending)
        self._pending = None
        result = dict(pending["result"])
        result["dry_run"] = False
        result["promoted"] = True
        return result

    def _commit(self, built):
        # 每次折叠都是一份完整快照：只替换同一业务域的分组，其它域原样保留。
        domain = built["result"]["domain"]
        kept = OrderedDict(
            (gid, group)
            for gid, group in self.groups.items()
            if group["domain"] != domain
        )
        for gid, group in built["groups"].items():
            kept[gid] = group
        self.groups = kept
        for entry in built["audit"]:
            self._audit(entry)

    def explain(self, key):
        """按主记录或被合并记录的主键 / row_id 回看取舍过程。"""
        key = str(key)
        group = self._lookup(key)
        if group is None:
            raise KeyError(f"没有找到记录 {key!r}")
        members = list(group["members"].values())
        focus = next(
            (
                dict(member["reason"])
                for member in members
                if key in (str(member["row_id"]), str(member["key"]))
            ),
            None,
        )
        status = "merged" if focus is not None else "master"
        return {
            "key": key,
            "status": status,
            "focus": focus,
            "group": group["id"],
            "domain": group["domain"],
            "master": {
                "row_id": group["master_id"],
                "key": group["master_key"],
                "row": dict(group["master"]),
            },
            "merged": [
                {
                    "row_id": member["row_id"],
                    "key": member["key"],
                    "source": member["source"],
                    "row": dict(member["row"]),
                    "reason": dict(member["reason"]),
                }
                for member in members
            ],
            "reasons": [dict(member["reason"]) for member in members],
            "sources": [group["master"].get(self.source)]
            + [member["source"] for member in members],
            "events": [dict(event) for event in group["log"]],
        }

    def split(self, key, record_ids=None):
        """人工拆分误合并的记录：恢复为独立主记录并写审计。"""
        group = self._lookup(key)
        if group is None:
            raise KeyError(f"没有找到记录 {key!r}")
        candidates = {
            member["row_id"]: member for member in group["members"].values()
        }
        if record_ids is None:
            requested = list(candidates)
        else:
            requested = list(record_ids)
        if not requested:
            raise ValueError("没有可拆分的被合并记录")

        resolved = []
        for identifier in requested:
            if identifier == group["master_id"]:
                raise ValueError("主记录不能拆分；请指定被合并记录的 row_id")
            if identifier in candidates:
                resolved.append(identifier)
                continue
            by_key = [
                row_id
                for row_id, member in candidates.items()
                if member["key"] == identifier
            ]
            if len(by_key) != 1:
                elsewhere = self._lookup(identifier)
                if elsewhere is not None and elsewhere["id"] != group["id"]:
                    raise KeyError(
                        f"{identifier!r} 不在分组 {group['id']}，"
                        f"而在分组 {elsewhere['id']}；请用该分组的主记录调用 split"
                    )
                raise KeyError(
                    f"{identifier!r} 不在分组 {group['id']} 的被合并记录中"
                )
            resolved.extend(by_key)

        now = _now_iso(self._clock)
        detached = []
        for row_id in OrderedDict.fromkeys(resolved):
            member = group["members"].pop(row_id)
            new_gid = self._new_gid()
            self.frozen.add(row_id)
            event = {
                "fold": group["fold"],
                "action": "split",
                "at": now,
                "row": row_id,
                "key": member["key"],
                "from_group": group["id"],
                "why": (
                    f"人工拆分：{row_id} 从 {group['master_id']} 恢复为独立主记录"
                ),
            }
            new_group = {
                "id": new_gid,
                "domain": group["domain"],
                "master_id": member["row_id"],
                "master_key": member["key"],
                "master": dict(member["row"]),
                "keys": [member["key"]],
                "members": OrderedDict(),
                "log": [event],
                "created_at": now,
                "updated_at": now,
                "fold": group["fold"],
            }
            self.groups[new_gid] = new_group
            group["log"].append(event)
            group["updated_at"] = now
            group["keys"] = [group["master_key"]] + [
                item["key"] for item in group["members"].values()
            ]
            self._audit(
                {
                    "action": "split",
                    "domain": group["domain"],
                    "group": group["id"],
                    "detached_group": new_gid,
                    "key": member["key"],
                    "row": row_id,
                    "why": event["why"],
                }
            )
            detached.append(self._group_view(new_group))

        return {"group": self._group_view(group), "detached": detached}

    def unsplit(self, row_id):
        """撤销人工拆分限制，允许该记录重新参与自动合并。"""
        if row_id not in self.frozen:
            raise KeyError(f"{row_id!r} 不在人工拆分名单中")
        self.frozen.discard(row_id)
        return self._audit(
            {
                "action": "unsplit",
                "row": row_id,
                "why": f"撤销 {row_id} 的拆分限制，恢复自动合并",
            }
        )

    def replay(self, key):
        """按记录回放合并过程：选主、补字段、合并、主记录变更、拆分。"""
        group = self._lookup(key)
        if group is None:
            raise KeyError(f"没有找到记录 {key!r}")
        steps = []
        for index, event in enumerate(group["log"]):
            step = dict(event)
            step["step"] = index + 1
            steps.append(step)
        return {
            "key": key,
            "group": group["id"],
            "master": group["master_id"],
            "steps": steps,
        }

    def audit(self, key=None):
        """审计日志；传入 key 时只返回该记录所在分组的相关条目。"""
        if key is None:
            return [dict(entry) for entry in self.audit_log]
        group = self._lookup(key)
        if group is None:
            raise KeyError(f"没有找到记录 {key!r}")
        gids = {group["id"]}
        return [
            dict(entry)
            for entry in self.audit_log
            if entry.get("group") in gids or entry.get("detached_group") in gids
        ]

    def export(self, key=None, path=None):
        """导出 JSON 可序列化的明细；path 不为空时同时写文件。"""
        if key is None:
            data = {
                "groups": [self._group_view(group) for group in self.groups.values()],
                "audit": [dict(entry) for entry in self.audit_log],
                "frozen": sorted(self.frozen),
            }
        else:
            data = self.explain(key)
            data["replay"] = self.replay(key)["steps"]
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, default=str)
        return data

    def export_json(self, path, key=None):
        return self.export(key=key, path=path)


# ---------------------------------------------------------------------------
# 模块级便捷接口（共享一个默认会话）
# ---------------------------------------------------------------------------

_default = RowFold()


def fold(rows, *, domain=None, dry_run=False):
    return _default.fold(rows, domain=domain, dry_run=dry_run)


def dry_run(rows, *, domain=None):
    return _default.dry_run(rows, domain=domain)


def promote(domain=None):
    return _default.promote(domain)


def explain(key):
    return _default.explain(key)


def split(key, record_ids=None):
    return _default.split(key, record_ids)


def unsplit(row_id):
    return _default.unsplit(row_id)


def replay(key):
    return _default.replay(key)


def audit(key=None):
    return _default.audit(key)


def export(key=None, path=None):
    return _default.export(key=key, path=path)


def export_json(path, key=None):
    return _default.export_json(path, key=key)


def reset():
    """清空默认会话（测试或切换业务域时使用）。"""
    global _default
    _default = RowFold()
