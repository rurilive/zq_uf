"""正文快照：失败回退、新旧对比、抽样切换、保留期回退。

改版流程：
    store.stage(key, html)      先抽一份候选，不覆盖线上正文
    store.diff(key)             对比候选与当前正文，看有没有丢段落
    store.promote(key)          确认没丢段落后再全量切换
    store.rollback(key)         有问题时退回上一版

抽取失败时 extract_safe() 直接返回上一次结果，并标 stale=True，
人工对比确认后可用 force=True 覆盖。
"""
import hashlib
import json
import os
import re
import tempfile
import time
from bisect import bisect_left
from collections import defaultdict, deque
from difflib import SequenceMatcher

from .config import get_config
from .extract import extract

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def split_paragraphs(text):
    """把正文切成段落；中文长段落再按句拆，便于段落级对比。"""
    paragraphs = []
    for block in (text or "").split("\n\n"):
        block = " ".join(block.split())
        if not block:
            continue
        if len(block) <= 200:
            paragraphs.append(block)
            continue
        buffer = ""
        for sentence in _SENTENCE_SPLIT.split(block):
            buffer += sentence
            if len(buffer) >= 100:
                paragraphs.append(buffer.strip())
                buffer = ""
        if buffer.strip():
            paragraphs.append(buffer.strip())
    return paragraphs


def _normalize(paragraph):
    return re.sub(r"\s+", "", paragraph or "").lower()


def _is_same_paragraph(score, shortest):
    """短段落要求几乎逐字相同，长段落允许错别字/补一句的改写。"""
    return score >= (0.97 if shortest < 30 else 0.92)


def _align(old, new):
    """对齐两版段落，返回 (matched, missing, added) 三组下标。

    分两步：
    1. 精确相同的段落先配对，作为锚点（段落调换顺序不算丢）；
    2. 锚点之间的空档再按原顺序做单调模糊对齐，识别小幅改写。
    锚点把整页切成小段，空档通常只有几段，代价可控；
    单调对齐也是刻意的：任意两两匹配会让内容相近的段落互相顶替，
    掩盖真正丢失的那一段。
    """
    old_norm = [_normalize(p) for p in old]
    new_norm = [_normalize(p) for p in new]
    rows, cols = len(old_norm), len(new_norm)

    # 1. 精确匹配
    pool = defaultdict(deque)
    for j, text in enumerate(new_norm):
        pool[text].append(j)
    pairs = []
    for i, text in enumerate(old_norm):
        if pool[text]:
            pairs.append((i, pool[text].popleft()))

    matched = len(pairs)
    matched_old = {i for i, _ in pairs}
    matched_new = {j for _, j in pairs}

    # 锚点按 j 递增取最长递增子序列，避免段落调换顺序时把人切成负向空档
    anchors = _monotonic_pairs(pairs)

    missing = {i for i in range(rows) if i not in matched_old}
    added = [j for j in range(cols) if j not in matched_new]

    # 2. 锚点之间的空档逐段模糊对齐
    bounds = [(-1, -1)] + anchors + [(rows, cols)]
    for (left_old, left_new), (right_old, right_new) in zip(bounds, bounds[1:]):
        gap_old = [i for i in range(left_old + 1, right_old) if i not in matched_old]
        gap_new = [j for j in range(left_new + 1, right_new) if j not in matched_new]
        if not gap_old or not gap_new:
            continue
        # 空档大了就改走线性贪心：单调对齐是 O(n*m) 次编辑距离计算，
        # 整页几乎全改写时会把一次对比拖到十几秒。
        if len(gap_old) * len(gap_new) > 2500:
            sub_matched, sub_missing, sub_added = _align_greedy(
                [old_norm[i] for i in gap_old],
                [new_norm[j] for j in gap_new],
            )
        else:
            sub_matched, sub_missing, sub_added = _align_monotonic(
                [old_norm[i] for i in gap_old],
                [new_norm[j] for j in gap_new],
            )
        matched += sub_matched
        missing.difference_update(
            gap_old[k] for k in range(len(gap_old)) if k not in sub_missing
        )
        # 对齐上的新段落不再算新增
        sub_added_set = set(sub_added)
        for k, j in enumerate(gap_new):
            if k not in sub_added_set and j in added:
                added.remove(j)
    return matched, missing, added


def _monotonic_pairs(pairs):
    """从 (old, new) 配对里取 new 递增的最长子序列，作为对齐锚点。"""
    if not pairs:
        return []
    tails, tails_idx, prev = [], [], [-1] * len(pairs)
    for index, (_, j) in enumerate(pairs):
        pos = bisect_left(tails, j)
        if pos == len(tails):
            tails.append(j)
            tails_idx.append(index)
        else:
            tails[pos] = j
            tails_idx[pos] = index
        prev[index] = tails_idx[pos - 1] if pos > 0 else -1
    chain = []
    current = tails_idx[-1]
    while current != -1:
        chain.append(pairs[current])
        current = prev[current]
    return list(reversed(chain))


def _similarity(left, right):
    if not left or not right:
        return 1.0 if left == right else 0.0
    if left == right or left in right or right in left:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _align_greedy(old_norm, new_norm, window=3):
    """大空档的线性对齐：顺序前进，容忍窗口内的增删。

    假设一段正文在新版本里最多前后挪动 window 段，
    这样一段被删掉后不会让后面所有段落依次错位、被误报为丢失。
    只在空档很大时才用，小空档仍走精确的单调对齐。
    """
    def same(i, j):
        if not (0 <= i < len(old_norm) and 0 <= j < len(new_norm)):
            return False
        return _is_same_paragraph(
            _similarity(old_norm[i], new_norm[j]),
            min(len(old_norm[i]), len(new_norm[j])),
        )

    i = j = 0
    matched = 0
    matched_new = set()
    missing = set()
    added = []
    while i < len(old_norm) and j < len(new_norm):
        if same(i, j):
            matched += 1
            matched_new.add(j)
            i += 1
            j += 1
            continue
        skipped_new = next(
            (delta for delta in range(1, window + 1) if same(i, j + delta)),
            None,
        )
        skipped_old = next(
            (delta for delta in range(1, window + 1) if same(i + delta, j)),
            None,
        )
        if skipped_new is not None and skipped_old is not None:
            # 两边都能对上：旧段被新段整段替换
            missing.add(i)
            added.append(j)
            i += 1
            j += 1
        elif skipped_new is not None:
            # 新版本在此插入了 skipped_new 段
            added.extend(range(j, j + skipped_new))
            j += skipped_new
        elif skipped_old is not None:
            # 旧版本在此有 skipped_old 段被删掉
            missing.update(range(i, i + skipped_old))
            i += skipped_old
        else:
            # 窗口内对不上：旧段丢失、新段新增，同时前进
            missing.add(i)
            added.append(j)
            i += 1
            j += 1

    missing.update(range(i, len(old_norm)))
    added.extend(j for j in range(j, len(new_norm)) if j not in matched_new)
    return matched, missing, sorted(set(added))


def _align_monotonic(old_norm, new_norm):
    """单调对齐剩余段落，返回 (matched, missing 下标, added 下标)。"""
    rows, cols = len(old_norm), len(new_norm)
    weights = [[0.0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            left, right = old_norm[i], new_norm[j]
            longest = max(len(left), len(right)) or 1
            # 长度差超过 1/3 的段落不可能是同一段的改写，跳过昂贵的编辑距离
            if abs(len(left) - len(right)) / float(longest) > 0.34:
                continue
            if left in right or right in left:
                score = 1.0
            else:
                score = SequenceMatcher(None, left, right).ratio()
            if _is_same_paragraph(score, min(len(left), len(right))):
                weights[i][j] = score

    # best[i][j]：old[i:] 与 new[j:] 的最优对齐得分
    best = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        for j in range(cols - 1, -1, -1):
            options = [best[i + 1][j], best[i][j + 1]]
            if weights[i][j]:
                options.append(weights[i][j] + best[i + 1][j + 1])
            best[i][j] = max(options)

    matched = 0
    matched_new = set()
    missing = set()
    i = j = 0
    while i < rows and j < cols:
        if weights[i][j] and abs(
            best[i][j] - (weights[i][j] + best[i + 1][j + 1])
        ) < 1e-9:
            matched += 1
            matched_new.add(j)
            i += 1
            j += 1
        elif best[i + 1][j] >= best[i][j + 1]:
            missing.add(i)
            i += 1
        else:
            j += 1
    missing.update(range(i, rows))
    added = [j for j in range(cols) if j not in matched_new]
    return matched, missing, added


def compare_text(old_text, new_text):
    """按段落比较两版正文。

    返回 missing（旧有新无）、added（新有旧无）、retained（旧段落仍保留的比例）。
    missing 非空说明切换会丢正文段落，应先人工确认；
    细微改写（错别字、补一句话）不算丢失。
    """
    old = split_paragraphs(old_text)
    new = split_paragraphs(new_text)
    if not old and not new:
        return {"missing": [], "added": [], "retained": 1.0, "changed": False}

    old_norm = [_normalize(p) for p in old]
    new_norm = [_normalize(p) for p in new]
    matched, missing_indexes, added_indexes = _align(old, new)
    retained = matched / float(len(old)) if old else 1.0
    return {
        "missing": [old[i] for i in sorted(missing_indexes)],
        "added": [new[j] for j in sorted(added_indexes)],
        "retained": round(retained, 4),
        "changed": old_norm != new_norm,
    }


def is_failure(result, min_confidence=0.2):
    """抽取结果是否不可用：空正文，或把握程度低到需要人工看。"""
    if not result or not (result.get("text") or "").strip():
        return True
    if result.get("page_type") == "empty":
        return True
    return float(result.get("confidence", 0.0)) < float(min_confidence)


def _empty_result(reason):
    return {
        "text": "", "blocks": 0, "kept": [], "noise": [], "dropped": [],
        "confidence": 0.0, "short": False, "fallback": False,
        "page_type": "empty", "site": None, "error": reason,
    }


def _safe_extract(html, site=None, overrides=None):
    """抽取异常也转成失败结果，保证调用方总能走“保留上次正文”的分支。"""
    try:
        return extract(html, site=site, overrides=overrides)
    except Exception as exc:  # 页面结构异常不应让整条采集链断掉
        return _empty_result("%s: %s" % (type(exc).__name__, exc))


class MissingParagraphs(ValueError):
    """切换会丢掉旧正文段落，需人工确认。"""


class PageStore:
    """按 key（通常是 URL 或页面 ID）保存正文快照。"""

    def __init__(self, root, retention_days=None, clock=None):
        self.root = os.path.abspath(root)
        self.clock = clock or time.time
        self.retention_days = retention_days

    # ---------- 路径 ----------

    def _dir(self, key):
        digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:16]
        safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(key))[:40] or "page"
        return os.path.join(self.root, "%s-%s" % (safe, digest))

    def _read(self, path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def _write(self, path, payload):
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=1)
            os.replace(temp, path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    def _snapshot(self, result, source="extract"):
        snapshot = {
            "created_at": self.clock(),
            "source": source,
            "text": result.get("text", ""),
            "confidence": result.get("confidence", 0.0),
            "page_type": result.get("page_type", "empty"),
            "kept": result.get("kept", []),
            "noise": result.get("noise", []),
            "dropped": result.get("dropped", []),
            "short": result.get("short", False),
            "fallback": result.get("fallback", False),
            "site": result.get("site"),
        }
        if result.get("error"):
            snapshot["error"] = result["error"]
        return snapshot

    # ---------- 读取 ----------

    def current(self, key):
        return self._read(os.path.join(self._dir(key), "current.json"))

    def staged(self, key):
        return self._read(os.path.join(self._dir(key), "staged.json"))

    def history(self, key):
        """按时间从旧到新返回历史正文。"""
        return [snapshot for _, snapshot in self._history_items(key)]

    def _history_items(self, key):
        directory = os.path.join(self._dir(key), "history")
        if not os.path.isdir(directory):
            return []
        items = []
        for name in sorted(os.listdir(directory)):
            if name.endswith(".json"):
                snapshot = self._read(os.path.join(directory, name))
                if snapshot:
                    items.append((name, snapshot))
        return items

    # ---------- 写入 ----------

    def _archive(self, key, snapshot):
        directory = os.path.join(self._dir(key), "history")
        os.makedirs(directory, exist_ok=True)
        stamp = "%012d" % int(float(snapshot.get("created_at", self.clock())) * 1000)
        base = os.path.join(directory, stamp)
        path, suffix = base + ".json", 1
        while os.path.exists(path):
            path = "%s-%d.json" % (base, suffix)
            suffix += 1
        self._write(path, snapshot)

    def save(self, key, result, source="extract"):
        """保存为当前正文；旧版本自动进入 history，保留期内可回退。"""
        snapshot = self._snapshot(result, source)
        previous = self.current(key)
        if previous and previous.get("text") != snapshot["text"]:
            self._archive(key, previous)
        self._write(os.path.join(self._dir(key), "current.json"), snapshot)
        self.prune(key)
        return snapshot

    def stage(self, key, html, site=None, overrides=None):
        """抽取候选正文但不覆盖当前版本，供抽样对比。"""
        result = _safe_extract(html, site=site, overrides=overrides)
        snapshot = self._snapshot(result, "staged")
        snapshot["failed"] = is_failure(result)
        snapshot["error"] = result.get("error")
        self._write(os.path.join(self._dir(key), "staged.json"), snapshot)
        return snapshot

    def extract_safe(self, key, html, site=None, overrides=None, force=False,
                     min_confidence=0.2):
        """抽取并保存；失败时保留上一次结果，返回 stale=True 供人工确认。

        force=True 表示人工已确认，即使失败也用新结果覆盖。
        """
        result = _safe_extract(html, site=site, overrides=overrides)
        failed = is_failure(result, min_confidence)
        previous = self.current(key)
        if failed and previous and not force:
            return {
                "result": previous,
                "stale": True,
                "reason": "extract-failed",
                "confidence": result.get("confidence", 0.0),
                "candidate": result,
            }
        if failed and not previous and not force:
            # 没有旧正文可保留，也不写入空快照，交给人工处理
            return {
                "result": None,
                "stale": True,
                "reason": "extract-failed-no-previous",
                "confidence": result.get("confidence", 0.0),
                "candidate": result,
            }
        snapshot = self.save(key, result, source="forced" if failed else "extract")
        return {
            "result": snapshot,
            "stale": False,
            "reason": "forced-overwrite" if (failed and force) else "ok",
            "confidence": snapshot["confidence"],
            "candidate": result,
        }

    # ---------- 对比与切换 ----------

    def diff(self, key):
        """候选与当前正文的段落差异；没有候选时返回 None。"""
        staged = self.staged(key)
        if not staged:
            return None
        current = self.current(key)
        diff = compare_text(current["text"] if current else "", staged["text"])
        diff["staged_failed"] = bool(staged.get("failed"))
        diff["staged_confidence"] = staged.get("confidence", 0.0)
        if current:
            diff["current_confidence"] = current.get("confidence", 0.0)
        return diff

    def promote(self, key, require_no_missing=True):
        """把候选切换为当前正文。

        候选丢了旧段落或本身抽取失败时，默认拒绝；
        人工确认后可传 require_no_missing=False 强制切换。
        """
        staged = self.staged(key)
        if not staged:
            raise ValueError("没有待切换的候选正文")
        diff = self.diff(key) or {}
        if diff.get("missing") and require_no_missing:
            raise MissingParagraphs(
                "候选缺少 %d 个旧正文段落，先人工确认" % len(diff["missing"])
            )
        if staged.get("failed") and require_no_missing:
            raise MissingParagraphs("候选正文为空或把握程度过低，先人工确认")
        snapshot = dict(staged)
        snapshot["source"] = "promote"
        snapshot["created_at"] = self.clock()
        previous = self.current(key)
        if previous:
            self._archive(key, previous)
        self._write(os.path.join(self._dir(key), "current.json"), snapshot)
        self.discard_staged(key)
        self.prune(key)
        return snapshot

    def discard_staged(self, key):
        try:
            os.unlink(os.path.join(self._dir(key), "staged.json"))
        except OSError:
            pass

    def rollback(self, key):
        """退回最近一次被替换的正文；当前版本也保留在 history。"""
        items = self._history_items(key)
        if not items:
            raise ValueError("没有可回退的历史正文")
        name, target = items[-1]
        current = self.current(key)
        if current:
            self._archive(key, current)
        restored = dict(target)
        restored["source"] = "rollback"
        restored["created_at"] = self.clock()
        self._write(os.path.join(self._dir(key), "current.json"), restored)
        # 该历史项已被恢复，从 history 移除，避免重复回退到同一条
        try:
            os.unlink(os.path.join(self._dir(key), "history", name))
        except OSError:
            pass
        return restored

    # ---------- 保留期 ----------

    def prune(self, key=None, now=None):
        """删除超过保留期的历史正文，默认保留 pagecore 配置的 7 天。

        key 为空时清理整个 root 下所有页面。
        """
        now = self.clock() if now is None else now
        days = self.retention_days
        if days is None:
            days = int(get_config()["retention_days"])
        cutoff = now - days * 86400
        if key is not None:
            directories = [os.path.join(self._dir(key), "history")]
        else:
            directories = []
            if os.path.isdir(self.root):
                for name in sorted(os.listdir(self.root)):
                    path = os.path.join(self.root, name, "history")
                    if os.path.isdir(path):
                        directories.append(path)
        removed = 0
        for directory in directories:
            if not os.path.isdir(directory):
                continue
            for name in os.listdir(directory):
                path = os.path.join(directory, name)
                snapshot = self._read(path)
                if snapshot is None or float(snapshot.get("created_at", 0)) < cutoff:
                    try:
                        os.unlink(path)
                        removed += 1
                    except OSError:
                        pass
        return removed


def compare_batch(pairs):
    """批量对比：pairs 为 [(旧正文, 新正文)]，汇总是否整体丢段落。"""
    total = {"missing": 0, "pages": 0, "lossy_pages": 0, "details": []}
    for index, (old_text, new_text) in enumerate(pairs):
        diff = compare_text(old_text, new_text)
        total["pages"] += 1
        total["missing"] += len(diff["missing"])
        if diff["missing"]:
            total["lossy_pages"] += 1
            total["details"].append({
                "index": index,
                "missing": diff["missing"][:5],
                "retained": diff["retained"],
            })
    return total


def stage_batch(store, items, site=None, overrides=None):
    """抽样切换：对 items=[(key, html)] 逐页生成候选并汇总差异。

    全部页面都没有丢旧段落（safe=True）才建议全量切换；
    之后仍可逐页 store.promote(key) 或 store.promote(key, require_no_missing=False)。
    """
    details = []
    for key, html in items:
        staged = store.stage(key, html, site=site, overrides=overrides)
        diff = store.diff(key) or {}
        details.append({
            "key": key,
            "confidence": staged.get("confidence", 0.0),
            "failed": bool(staged.get("failed")),
            "missing": diff.get("missing", []),
            "added": len(diff.get("added", [])),
            "retained": diff.get("retained", 1.0),
        })
    lossy = [item for item in details if item["missing"] or item["failed"]]
    return {
        "pages": len(details),
        "lossy_pages": len(lossy),
        "safe": not lossy,
        "details": details,
    }


__all__ = [
    "PageStore", "MissingParagraphs", "compare_text", "compare_batch",
    "stage_batch", "is_failure", "split_paragraphs",
]
