"""URL 等价类增量索引。

设计要点：
- 增量入队：新链接只查归一化 key 索引，命中即并入已有类，不全量重建；
- 代表稳定：类代表取成员中最早出现（seq 最小）的 URL，合并时保留更老的代表；
- 规则变更：只检测/重建 key 发生变化的类，影子构建后原子切换，查询永远看不到半成品；
- 可拆分可合并：重建时旧类成员按新 key 重新分组，天然支持 split/merge；
- 可恢复：JSON 快照落盘，恢复后校验 key，规则漂移只重算受影响类；
- 可清理：remove/remove_many 清理失效链接及其关系。
"""

import json
import os
import threading

from .rules import Normalizer


class _Entry:
    __slots__ = ("url", "seq", "key", "fired", "class_id")

    def __init__(self, url, seq, key, fired, class_id):
        self.url = url
        self.seq = seq
        self.key = key
        self.fired = tuple(fired)
        self.class_id = class_id


class _Class:
    __slots__ = ("id", "representative", "entries")

    def __init__(self, class_id, representative):
        self.id = class_id
        self.representative = representative
        self.entries = {}  # url -> _Entry


class URLNormIndex:
    def __init__(self, rules=None):
        self._normalizer = Normalizer(rules)
        self._entries = {}        # url -> _Entry
        self._by_key = {}         # 归一化 key -> _Class
        self._classes = {}        # class_id -> _Class
        self._rule_deps = {}      # rule_id -> set(class_id)，观测用/候选筛选
        self._next_seq = 0
        self._next_cid = 1
        self._lock = threading.RLock()
        self.recovery = None      # load() 之后记录恢复情况

    # ---------------------------------------------------------------- 查询

    def same(self, a, b):
        with self._lock:
            return self._normalizer.match(a, b)

    def class_of(self, url):
        """返回 URL 当前所属类 id；未入队返回 None。"""
        with self._lock:
            entry = self._entries.get(url or "")
            return None if entry is None else entry.class_id

    def representative_of(self, url):
        with self._lock:
            entry = self._entries.get(url or "")
            return None if entry is None else self._classes[entry.class_id].representative

    def class_info(self, class_id):
        with self._lock:
            cls = self._classes.get(class_id)
            if cls is None:
                return None
            return {
                "class_id": cls.id,
                "representative": cls.representative,
                "size": len(cls.entries),
                "members": sorted(cls.entries, key=lambda u: self._entries[u].seq),
            }

    def size(self):
        """等价类规模总览。"""
        with self._lock:
            return {
                "classes": len(self._classes),
                "urls": len(self._entries),
                "class_sizes": {cid: len(cls.entries) for cid, cls in self._classes.items()},
            }

    def stats(self):
        with self._lock:
            return {
                "classes": len(self._classes),
                "urls": len(self._entries),
                "rule_versions": self._normalizer.versions(),
                "rule_dependents": {rid: sorted(cids) for rid, cids in self._rule_deps.items()},
            }

    def classes(self):
        with self._lock:
            return [self.class_info(cid) for cid in sorted(self._classes)]

    # ---------------------------------------------------------------- 入队

    def add(self, url):
        """增量并入。返回新增/合并/代表等信息，绝不触发全量重建。"""
        with self._lock:
            url = url or ""
            old = self._entries.get(url)
            if old is not None:
                cls = self._classes[old.class_id]
                return {
                    "url": url,
                    "action": "existing",
                    "created": 0,
                    "merged": 0,
                    "class_id": cls.id,
                    "representative": cls.representative,
                    "class_size": len(cls.entries),
                    "classes": len(self._classes),
                    "key": old.key,
                    "fired_rules": list(old.fired),
                    "merged_from": [],
                }

            key, fired = self._normalizer.normalize(url)
            seq = self._next_seq
            self._next_seq += 1

            cls = self._by_key.get(key)
            if cls is None:
                cid = self._next_cid
                self._next_cid += 1
                cls = _Class(cid, url)
                self._classes[cid] = cls
                self._by_key[key] = cls
                action, created, merged = "created", 1, 0
                merged_from = []
            else:
                action, created, merged = "merged", 0, 1
                merged_from = [cls.id]

            entry = _Entry(url, seq, key, fired, cls.id)
            cls.entries[url] = entry
            self._entries[url] = entry
            for rid in fired:
                self._rule_deps.setdefault(rid, set()).add(cls.id)
            return {
                "url": url,
                "action": action,
                "created": created,
                "merged": merged,
                "class_id": cls.id,
                "representative": cls.representative,
                "class_size": len(cls.entries),
                "classes": len(self._classes),
                "key": key,
                "fired_rules": list(fired),
                "merged_from": merged_from,
            }

    # ---------------------------------------------------------------- 失效清理

    def remove(self, url):
        """清理一条已失效链接及其关系；类清空则删除，代表失效则顺延最早成员。"""
        with self._lock:
            return self._remove_one(url or "")

    def remove_many(self, urls):
        with self._lock:
            results = [self._remove_one(u or "") for u in urls]
            return {
                "removed": sum(r["removed"] for r in results),
                "classes_dropped": sum(1 for r in results if r["action"] == "class_dropped"),
                "details": results,
                "classes": len(self._classes),
                "urls": len(self._entries),
            }

    def _remove_one(self, url):
        entry = self._entries.pop(url, None)
        if entry is None:
            return {"url": url, "removed": 0, "action": "not_found",
                    "representative": None, "class_id": None}
        cls = self._classes[entry.class_id]
        del cls.entries[url]
        rep_changed = cls.representative == url
        if not cls.entries:
            del self._classes[cls.id]
            self._by_key.pop(entry.key, None)
            for deps in self._rule_deps.values():
                deps.discard(cls.id)
            action = "class_dropped"
        else:
            if rep_changed:
                cls.representative = min(cls.entries.values(), key=lambda e: e.seq).url
            action = "removed"
            self._refresh_deps_for_class(cls)
        return {
            "url": url,
            "removed": 1,
            "action": action,
            "class_id": cls.id if action == "removed" else entry.class_id,
            "representative": cls.representative if action == "removed" else None,
            "representative_changed": rep_changed and action == "removed",
            "class_size": len(cls.entries),
            "classes": len(self._classes),
        }

    def _refresh_deps_for_class(self, cls):
        fired = set()
        for entry in cls.entries.values():
            fired.update(entry.fired)
        for rid, deps in self._rule_deps.items():
            if rid in fired:
                deps.add(cls.id)
            else:
                deps.discard(cls.id)

    # ---------------------------------------------------------------- 规则变更 / 局部重算

    def update_rule(self, rule_id, apply, version=None, progress=None):
        """替换规则并只重算受影响类。

        检测覆盖全部类（旧依赖索引无法覆盖"新版本才开始 fire"的类），
        但只有 key 真正变化的类才会被重建。
        """
        with self._lock:
            rule = self._normalizer.replace_rule(rule_id, apply, version)
            report = self.recompute(rule_id=None, progress=progress)
            report["rule"] = {"id": rule.id, "version": rule.version}
            return report

    def add_rule(self, rule, position=None, progress=None):
        with self._lock:
            self._normalizer.add_rule(rule, position)
            return self.recompute(rule_id=None, progress=progress)

    def recompute(self, rule_id=None, progress=None):
        """规则版本变化后的局部重算。

        - rule_id 给定时，仅以旧依赖索引中的候选类为检测范围；否则全量检测；
        - 只有 key 真正变化的类才进入重建（split/merge 在影子索引里完成）；
        - 切换是原子的，progress 回调中发起的查询读到的仍是切换前的一致快照。
        """
        with self._lock:
            def emit(stage, detail):
                if progress is not None:
                    progress(stage, dict(detail))

            emit("start", {"rule_id": rule_id, "classes": len(self._classes)})

            # Phase 1：检测受影响类（活索引保持可读）
            if rule_id is None:
                candidate_ids = set(self._classes)
            else:
                candidate_ids = {cid for cid in self._rule_deps.get(rule_id, ())
                                 if cid in self._classes}
            affected = []          # (cls, {url: (new_key, new_fired)})
            for cid in sorted(candidate_ids):
                cls = self._classes[cid]
                url_results = {}
                changed = False
                for url, entry in cls.entries.items():
                    new_key, new_fired = self._normalizer.normalize(url)
                    url_results[url] = (new_key, tuple(new_fired))
                    if new_key != entry.key:
                        changed = True
                if changed:
                    affected.append((cls, url_results))
            emit("detected", {"affected_class_ids": [cls.id for cls, _ in affected],
                              "candidates": sorted(candidate_ids)})
            if not affected:
                # key 都没变，但中间 fired 集合可能变化：用新 fired 校正条目与依赖索引
                for cid in sorted(candidate_ids):
                    if cid not in self._classes:
                        continue
                    cls = self._classes[cid]
                    for url, entry in list(cls.entries.items()):
                        new_key, new_fired = self._normalizer.normalize(url)
                        cls.entries[url] = _Entry(url, entry.seq, new_key,
                                                  tuple(new_fired), cls.id)
                        self._entries[url] = cls.entries[url]
                    self._refresh_deps_for_class(cls)
                emit("swap", {"rebuilt": 0})
                return self._recompute_report(rule_id, [], [], [], [])

            # Phase 2：影子重建。先原样复制不受影响类
            affected_ids = {cls.id for cls, _ in affected}
            unaffected_ids = set(self._classes) - affected_ids
            sh_classes = {}
            sh_by_key = {}
            sh_entries = {}
            for cid, cls in self._classes.items():
                if cid in affected_ids:
                    continue
                shadow = _Class(cls.id, cls.representative)
                shadow.entries = dict(cls.entries)
                sh_classes[cid] = shadow
                sh_by_key[next(iter(shadow.entries.values())).key] = shadow
                sh_entries.update(shadow.entries)

            # 收集受影响成员，按新 key 分组（保持 seq 顺序以便代表确定）
            harvested = []  # (url, seq, new_key, new_fired, old_cid)
            for cls, url_results in affected:
                for url, (new_key, new_fired) in url_results.items():
                    old = cls.entries[url]
                    harvested.append((url, old.seq, new_key, new_fired, cls.id))
            harvested.sort(key=lambda item: item[1])

            clusters = {}
            for url, seq, new_key, new_fired, old_cid in harvested:
                clusters.setdefault(new_key, []).append((url, seq, new_fired, old_cid))

            # 旧代表所在的新簇优先复用旧类 id，代表口径仍是最早出现
            old_rep_url_to_cid = {cls.representative: cls.id for cls, _ in affected}
            reused_ids = set()
            target_of_old = {}      # old_cid -> set(target_cid)
            sources_of_target = {}  # target_cid -> set(old_cid)

            for new_key in sorted(clusters, key=lambda k: min(h[1] for h in clusters[k])):
                members = clusters[new_key]
                target = sh_by_key.get(new_key)
                if target is None:
                    cid = None
                    for url, _seq, _fired, _old in members:
                        candidate = old_rep_url_to_cid.get(url)
                        if candidate is not None and candidate not in reused_ids:
                            cid = candidate
                            reused_ids.add(cid)
                            break
                    if cid is None:
                        cid = self._next_cid
                        self._next_cid += 1
                    target = _Class(cid, members[0][0])
                    sh_classes[cid] = target
                    sh_by_key[new_key] = target
                for url, seq, new_fired, old_cid in members:
                    target.entries[url] = _Entry(url, seq, new_key, new_fired, target.id)
                    sh_entries[url] = target.entries[url]
                    target_of_old.setdefault(old_cid, set()).add(target.id)
                    sources_of_target.setdefault(target.id, set()).add(old_cid)
                target.representative = min(target.entries.values(), key=lambda e: e.seq).url

            emit("rebuilt", {"shadow_classes": len(sh_classes), "urls": len(sh_entries)})

            # 汇总 split / merge
            splits, merges = {}, {}
            for old_cid, targets in target_of_old.items():
                if len(targets) > 1:
                    splits[old_cid] = sorted(targets)
            for target_cid, sources in sources_of_target.items():
                # 多旧类合一，或受影响类并入未受影响的旧类，才算 merge；
                # 从单个旧类拆出来的新 id 只出现在 splits 里。
                joined_unaffected = target_cid in unaffected_ids
                if len(sources) > 1 or joined_unaffected:
                    merges[target_cid] = sorted(sources)

            # Phase 3：原子切换，并整体重建规则依赖索引
            self._classes = sh_classes
            self._by_key = sh_by_key
            self._entries = sh_entries
            self._rule_deps = {}
            for entry in self._entries.values():
                for rid in entry.fired:
                    self._rule_deps.setdefault(rid, set()).add(entry.class_id)
            self._next_cid = max(self._classes, default=0) + 1
            emit("swap", {"rebuilt": len(affected)})

            return self._recompute_report(
                rule_id,
                [cls.id for cls, _ in affected],
                sorted(splits.items()),
                sorted(merges.items()),
                [
                    {"class_id": cls.id, "representative": cls.representative,
                     "size": len(cls.entries)}
                    for cls in sh_classes.values()
                ],
            )

    @staticmethod
    def _recompute_report(rule_id, recomputed, splits, merges, classes_after):
        return {
            "rule_id": rule_id,
            "recomputed_class_ids": recomputed,
            "recomputed": len(recomputed),
            "splits": [{"from": old, "to": targets} for old, targets in splits],
            "merges": [{"target": target, "from": sources} for target, sources in merges],
            "classes_after": classes_after,
        }

    # ---------------------------------------------------------------- 落盘 / 恢复

    def snapshot(self):
        with self._lock:
            return {
                "format": 1,
                "rule_versions": self._normalizer.versions(),
                "next_seq": self._next_seq,
                "next_cid": self._next_cid,
                "classes": [
                    {
                        "id": cls.id,
                        "representative": cls.representative,
                        "members": [url for url, _ in sorted(
                            cls.entries.items(), key=lambda kv: kv[1].seq)],
                    }
                    for cls in (self._classes[cid] for cid in sorted(self._classes))
                ],
                "entries": [
                    {"url": e.url, "seq": e.seq, "key": e.key, "fired": list(e.fired)}
                    for e in sorted(self._entries.values(), key=lambda e: e.seq)
                ],
            }

    def save(self, path):
        """原子落盘：同目录临时文件 + os.replace，崩溃不会留下半份索引。"""
        directory = os.path.dirname(os.path.abspath(path))
        tmp = os.path.join(directory, ".%s.tmp" % os.path.basename(path))
        with self._lock:
            payload = json.dumps(self.snapshot(), ensure_ascii=False, indent=2, sort_keys=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return {"path": os.path.abspath(path), "classes": len(self._classes),
                "urls": len(self._entries)}

    @classmethod
    def load(cls, path, rules=None, progress=None):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("format") != 1:
            raise ValueError("unsupported snapshot format: %r" % data.get("format"))

        index = cls(rules=rules)
        snapshot_rules = data.get("rule_versions", {})
        current_rules = index._normalizer.versions()
        unknown = sorted(set(snapshot_rules) - set(current_rules))
        if unknown:
            raise ValueError(
                "snapshot references rules missing from current normalizer: %s" % unknown)
        rule_drift = {rid: {"snapshot": snapshot_rules[rid], "current": current_rules[rid]}
                      for rid in snapshot_rules
                      if snapshot_rules[rid] != current_rules.get(rid)}

        index._next_seq = data["next_seq"]
        index._next_cid = data["next_cid"]
        entry_meta = {item["url"]: item for item in data["entries"]}
        for chunk in data["classes"]:
            cid = chunk["id"]
            cls = _Class(cid, chunk["representative"])
            for url in chunk["members"]:
                meta = entry_meta[url]
                cls.entries[url] = _Entry(url, meta["seq"], meta["key"],
                                          tuple(meta["fired"]), cid)
                index._entries[url] = cls.entries[url]
            index._classes[cid] = cls
            index._by_key[next(iter(cls.entries.values())).key] = cls
        for entry in index._entries.values():
            for rid in entry.fired:
                index._rule_deps.setdefault(rid, set()).add(entry.class_id)

        # 用当前规则校验 key；有漂移则只重算受影响类，恢复期间同样原子切换
        drifted = []
        for entry in index._entries.values():
            new_key, _ = index._normalizer.normalize(entry.url)
            if new_key != entry.key:
                drifted.append(entry.class_id)
        recompute_report = None
        if drifted:
            recompute_report = index.recompute(progress=progress)

        index.recovery = {
            "loaded_from": os.path.abspath(path),
            "classes": len(index._classes),
            "urls": len(index._entries),
            "rule_drift": rule_drift,
            "key_mismatch_classes": sorted(set(drifted)),
            "recomputed": None if recompute_report is None
            else recompute_report["recomputed_class_ids"],
        }
        return index
