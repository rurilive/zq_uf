"""页面快照与变更检测：剔除噪声后按结构块取指纹，变更定位到块。"""
import hashlib
import re
import time
from html.parser import HTMLParser

# 噪声：脚本、样式、模板等不参与块指纹
_NOISE_TAGS = {"script", "style", "noscript", "template", "iframe", "ins"}
# 噪声特征：广告、计数器、时间戳等 class/id 命中即整块剔除
_NOISE_ATTR_RE = re.compile(
    r"(^|[^a-z])(ad|ads|advert|banner|promo|sponsor|counter|count|visit|hits|timestamp)"
    r"([^a-z]|$)", re.I)
# 参与比对的结构块标签
_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "main", "aside",
    "nav", "li", "ul", "ol", "table", "blockquote", "pre", "form",
    "h1", "h2", "h3", "h4", "h5", "h6",
}
_VOID_TAGS = {
    "br", "img", "hr", "meta", "link", "input", "area", "base", "col",
    "embed", "source", "track", "wbr",
}

_WS_RE = re.compile(r"\s+")

# 误报样本登记：(before_fingerprint, after_fingerprint)，"" 表示块不存在
_FALSE_POSITIVES = set()


def mark_false_positive(before_fingerprint, after_fingerprint=""):
    """把一对前后指纹标记为误报，之后 diff 自动抑制该变更。"""
    _FALSE_POSITIVES.add((before_fingerprint, after_fingerprint))


def _normalize(text):
    return _WS_RE.sub(" ", text).strip()


def _fingerprint(tag, text):
    return hashlib.sha256((tag + "\n" + text).encode("utf-8")).hexdigest()[:16]


def _summary(text, limit=60):
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _BlockParser(HTMLParser):
    """取最外层结构块，块内文本合并；噪声/广告子树整体跳过。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._skip_depth = 0
        self._current = None  # [tag, [texts]]
        self._counters = {}

    @staticmethod
    def _is_noise(attrs):
        classes = ""
        for key, value in attrs:
            if key in ("class", "id") and value:
                classes += " " + value
        return bool(classes and _NOISE_ATTR_RE.search(classes))

    def handle_starttag(self, tag, attrs):
        if self._skip_depth:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if tag in _NOISE_TAGS or self._is_noise(attrs):
            if tag not in _VOID_TAGS:
                self._skip_depth = 1
            return
        if tag in _BLOCK_TAGS and self._current is None:
            self._current = [tag, []]

    def handle_endtag(self, tag):
        if self._skip_depth:
            if tag not in _VOID_TAGS:
                self._skip_depth -= 1
            return
        if self._current is not None and self._current[0] == tag:
            tag_, texts = self._current
            self._current = None
            text = _normalize(" ".join(texts))
            if text:
                self._counters[tag_] = self._counters.get(tag_, 0) + 1
                block_id = "%s#%d" % (tag_, self._counters[tag_])
                self.blocks.append({
                    "id": block_id,
                    "tag": tag_,
                    "text": text,
                    "fingerprint": _fingerprint(tag_, text),
                })

    def handle_data(self, data):
        if not self._skip_depth and self._current is not None:
            self._current[1].append(data)


def extract_blocks(html):
    """剔除脚本/样式/广告后，按结构块取指纹。"""
    parser = _BlockParser()
    parser.feed(html or "")
    parser.close()
    return parser.blocks


def snapshot(page, store=None):
    """采集页面快照。

    page: {"html": ..., "site": ..., "url": ..., "ts": ...}
    返回 {"site", "url", "ts", "hash", "blocks", "version"}。
    hash 是原始页哈希（兼容旧口径）；blocks 是噪声剔除后的块指纹。
    传入 store 时按站点持久化并分配递增版本号。
    """
    html = page.get("html") or ""
    snap = {
        "site": page.get("site") or "",
        "url": page.get("url") or "",
        "ts": page.get("ts", time.time()),
        "hash": hashlib.sha256(html.encode("utf-8")).hexdigest()[:16],
        "blocks": extract_blocks(html),
        "version": page.get("version", 1),
    }
    if store is not None:
        snap["version"] = store.save(snap)
    return snap


def _is_false_positive(before_fp, after_fp):
    return (before_fp, after_fp) in _FALSE_POSITIVES


def _pair_blocks(old_blocks, new_blocks):
    """配对前后块：先按指纹配对未变块（吸收位置移动），再按 id 配对修改块。

    返回 (modified_pairs, removed, added)。
    """
    remaining_new = {}
    for nb in new_blocks:
        remaining_new.setdefault(nb["fingerprint"], []).append(nb)
    removed = []
    for ob in old_blocks:
        same = remaining_new.get(ob["fingerprint"])
        if same:
            same.pop(0)  # 指纹相同视为未变
        else:
            removed.append(ob)
    unmatched_new = [nb for nbs in remaining_new.values() for nb in nbs]
    new_by_id = {nb["id"]: nb for nb in unmatched_new}
    modified, still_removed = [], []
    for ob in removed:
        nb = new_by_id.pop(ob["id"], None)
        if nb is None:
            still_removed.append(ob)
        else:
            modified.append((ob, nb))
    return modified, still_removed, list(new_by_id.values())


def diff(old, new, store=None):
    """按块比对两个快照，返回变更块、摘要和版本。

    变更块含 id、change(added/removed/modified)、前后摘要和块级样本，
    供运营复核；已标记的误报样本自动抑制。传入 store 时变更记录落库，
    可按站点和时间查询。
    """
    modified, removed, added = _pair_blocks(
        old.get("blocks", []), new.get("blocks", []))
    changes = []
    for ob, nb in modified:
        if _is_false_positive(ob["fingerprint"], nb["fingerprint"]):
            continue
        changes.append({
            "id": nb["id"], "change": "modified",
            "before": _summary(ob["text"]), "after": _summary(nb["text"]),
            "sample_before": ob["text"][:200], "sample_after": nb["text"][:200],
        })
    for ob in removed:
        if _is_false_positive(ob["fingerprint"], ""):
            continue
        changes.append({
            "id": ob["id"], "change": "removed",
            "before": _summary(ob["text"]), "after": "",
            "sample_before": ob["text"][:200], "sample_after": "",
        })
    for nb in added:
        if _is_false_positive("", nb["fingerprint"]):
            continue
        changes.append({
            "id": nb["id"], "change": "added",
            "before": "", "after": _summary(nb["text"]),
            "sample_before": "", "sample_after": nb["text"][:200],
        })
    counts = {"added": 0, "modified": 0, "removed": 0}
    for c in changes:
        counts[c["change"]] += 1
    if changes:
        summary = "新增%d 修改%d 删除%d：%s" % (
            counts["added"], counts["modified"], counts["removed"],
            ", ".join(c["id"] for c in changes))
    else:
        summary = "无变更"
    result = {
        "changed": bool(changes),
        "blocks": changes,
        "summary": summary,
        "version": new.get("version", 1),
    }
    if store is not None:
        store.record_diff(new.get("site") or old.get("site") or "", {
            "ts": new.get("ts", time.time()),
            "from_version": old.get("version", 1),
            "to_version": result["version"],
            "changed": result["changed"],
            "summary": result["summary"],
            "blocks": changes,
        })
    return result
