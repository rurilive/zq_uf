"""按块打分的正文抽取。

- 正文按内容密度和链接比例判断，导航、评论区、推荐位等噪声块单独标出；
- 短正文保留，只降低把握程度（confidence），不再整篇丢弃；
- 规则集中在 SiteConfig，站点改版只改配置，不动代码；
- Extractor 抽取失败时保留上一次结果（stale=True），人工用 diff_results
  对比新旧正文、确认没丢段落后再覆盖。
"""
from dataclasses import dataclass, replace
from html.parser import HTMLParser


class ExtractionError(Exception):
    """抽取失败且没有上一次结果可回退。"""


@dataclass(frozen=True)
class SiteConfig:
    """单站点抽取规则。改版时只调这里。"""

    noise_tags: tuple = ("nav", "header", "footer", "aside", "form")
    noise_keywords: tuple = (
        "nav", "menu", "sidebar", "footer", "comment",
        "recommend", "related", "promo", "advert", "breadcrumb",
    )
    max_link_ratio: float = 0.5        # 块内链接文字占比超过则视为噪声
    keep_ratio: float = 0.2            # 得分低于最佳块该比例则剔除
    min_block_chars: int = 10          # 参与打分的最小块字数
    short_body_chars: int = 200        # 正文低于该字数算短文
    short_body_confidence: float = 0.4  # 短文把握程度上限
    full_confidence_chars: int = 500   # 达到该字数把握程度满分


def _config(config):
    if config is None:
        return SiteConfig()
    if isinstance(config, SiteConfig):
        return config
    return replace(SiteConfig(), **config)  # dict 覆盖单站点规则


_BLOCK_TAGS = frozenset({
    "p", "div", "article", "section", "main", "nav", "header", "footer",
    "aside", "ul", "ol", "li", "dl", "dt", "dd", "table", "tr", "td", "th",
    "blockquote", "pre", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "form", "fieldset",
})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "title"})


class _Node:
    __slots__ = ("tag", "label", "text", "link", "tags", "children")

    def __init__(self, tag, label=""):
        self.tag = tag
        self.label = label          # class/id 小写拼接，用于噪声关键词匹配
        self.text = []              # 直接文本（含内联后代）
        self.link = []              # 其中 <a> 内的文本
        self.tags = 0               # 内部标签数，用于内容密度
        self.children = []


class _Parser(HTMLParser):
    """只为块级标签建节点，文本记到最内层块上，避免嵌套块重复计字。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self.stack = [self.root]
        self._links = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        self.stack[-1].tags += 1
        if tag == "a":
            self._links += 1
        if tag in _BLOCK_TAGS:
            label = " ".join(
                v for k, v in attrs if k in ("class", "id") and v
            ).lower()
            node = _Node(tag, label)
            self.stack[-1].children.append(node)
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if tag not in _SKIP_TAGS:
            self.stack[-1].tags += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if tag == "a" and self._links:
            self._links -= 1
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.stack[-1].text.append(text)
        if self._links:
            self.stack[-1].link.append(text)


def _noise_reason(node, cfg, inherited):
    if inherited:
        return inherited
    if node.tag in cfg.noise_tags:
        return "noise-tag"
    if any(k in node.label for k in cfg.noise_keywords):
        return "noise-keyword"
    return ""


def _collect(node, cfg, inherited, out):
    reason = _noise_reason(node, cfg, inherited)
    text = " ".join(node.text)
    if text:
        out.append({
            "tag": node.tag,
            "text": text,
            "chars": len(text),
            "link_chars": len(" ".join(node.link)),
            "tags": node.tags,
            "noise_reason": reason,
        })
    for child in node.children:
        _collect(child, cfg, reason, out)


def _classify(raw, cfg):
    out = []
    candidates = []
    for i, b in enumerate(raw):
        chars = b["chars"]
        link_ratio = round(b["link_chars"] / chars, 3)
        density = round(chars / (chars + b["tags"] * 5), 3)
        blk = {
            "index": i,
            "tag": b["tag"],
            "text": b["text"],
            "chars": chars,
            "link_ratio": link_ratio,
            "density": density,
            "score": round(chars * (1 - link_ratio) * density, 1),
            "kept": False,
            "reason": "",
        }
        if b["noise_reason"]:
            blk["reason"] = b["noise_reason"]
        elif link_ratio > cfg.max_link_ratio:
            blk["reason"] = "link-heavy"
        elif chars < cfg.min_block_chars:
            blk["reason"] = "too-short"
        else:
            blk["reason"] = "candidate"
            candidates.append(blk)
        out.append(blk)

    if candidates:
        anchor = max(b["score"] for b in candidates)
        for b in candidates:
            if b["score"] >= anchor * cfg.keep_ratio:
                b["kept"] = True
                b["reason"] = "content"
            else:
                b["reason"] = "below-threshold"

    if not any(b["kept"] for b in out):
        # 短文兜底：文章只有两三段时也要保留，把握程度在 confidence 里体现
        for b in out:
            if b["reason"] in ("too-short", "below-threshold") and b["chars"]:
                b["kept"] = True
                b["reason"] = "short-body"
    return out


def _confidence(chars, link_chars, cfg):
    if chars <= 0:
        return 0.0
    score = min(1.0, chars / cfg.full_confidence_chars)
    if link_chars:
        score *= 1 - 0.5 * min(1.0, link_chars / chars)
    if chars < cfg.short_body_chars:
        score = min(score, cfg.short_body_confidence)
    return round(score, 3)


def blocks(html, config=None):
    """返回页面的块列表，每块带得分、链接比例、保留/剔除标记和原因。"""
    cfg = _config(config)
    parser = _Parser()
    parser.feed(html or "")
    parser.close()
    raw = []
    _collect(parser.root, cfg, "", raw)
    return _classify(raw, cfg)


def extract(html, config=None):
    """抽取正文。

    返回正文文本、总字数、每块明细（哪些算正文、哪些被剔除及原因）、
    噪声块下标和整体把握程度。列表页这类通篇链接的页面正文为空、
    confidence 为 0，不会把导航和推荐位算进字数。
    """
    cfg = _config(config)
    blks = blocks(html, cfg)
    kept = [b for b in blks if b["kept"]]
    chars = sum(b["chars"] for b in kept)
    link_chars = sum(b["link_ratio"] * b["chars"] for b in kept)
    return {
        "text": "\n".join(b["text"] for b in kept),
        "chars": chars,
        "blocks": blks,
        "kept": [b["index"] for b in kept],
        "noise": [b["index"] for b in blks if b["reason"].startswith("noise")],
        "short": 0 < chars < cfg.short_body_chars,
        "confidence": _confidence(chars, round(link_chars), cfg),
    }


def diff_results(old, new):
    """对比两次抽取的正文块，返回新增/被删/保留的段落，供人工确认。"""
    old_kept = [b["text"] for b in old.get("blocks", []) if b.get("kept")]
    new_kept = [b["text"] for b in new.get("blocks", []) if b.get("kept")]
    old_set, new_set = set(old_kept), set(new_kept)
    return {
        "added": [t for t in new_kept if t not in old_set],
        "removed": [t for t in old_kept if t not in new_set],
        "unchanged": sum(1 for t in new_kept if t in old_set),
        "old_chars": old.get("chars", 0),
        "new_chars": new.get("chars", 0),
    }


class Extractor:
    """带兜底和人工确认的抽取器。

    抽取失败（异常或正文为空）时返回上一次成功结果并标记 stale=True，
    上一次结果不被覆盖；新结果先用 diff_results 与旧正文对比，人工确认
    没丢段落后再以成功的 extract 结果覆盖。
    """

    def __init__(self, config=None):
        self.config = config
        self.previous = None

    def extract(self, html):
        try:
            result = extract(html, self.config)
        except Exception:
            result = None
        if result is None or not result["text"].strip():
            if self.previous is None:
                raise ExtractionError("抽取失败且没有上一次结果可回退")
            stale = dict(self.previous)
            stale["stale"] = True
            return stale
        result["stale"] = False
        self.previous = result
        return result
