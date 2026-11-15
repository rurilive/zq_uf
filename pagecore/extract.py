"""按块打分的正文抽取。

- 正文/噪声按内容密度和链接比例区分，噪声块单独标出；
- 短正文保留，只给较低的把握程度；
- 规则可按站点覆盖（configure），改版只改配置；
- 失败时保留上一次结果（previous 参数或按 url 缓存），diff 对比两次正文。
"""
import difflib
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "article", "section", "main", "aside", "nav", "header",
    "footer", "ul", "ol", "li", "table", "blockquote", "pre", "form",
    "h1", "h2", "h3", "h4", "h5", "h6",
}
_VOID_TAGS = {
    "br", "img", "hr", "meta", "link", "input", "wbr", "source",
    "col", "embed", "area", "base", "track", "param",
}
_SKIP_TAGS = {"script", "style", "noscript", "textarea", "template"}
_PUNCT = "，。！？；、…：．.!?;:"

DEFAULT_RULES = {
    # 硬噪声：标签或 class/id 命中即剔除
    "noise_tags": ("nav", "aside", "footer", "header", "form"),
    "noise_patterns": (
        "nav", "menu", "sidebar", "comment", "recommend", "related",
        "advert", "promo", "breadcrumb", "share",
    ),
    # 链接文字占比超过该值视为导航/推荐位
    "link_density_max": 0.5,
    # 低于该字数的块先按噪声处理；若没有其它正文会被救回
    "min_block_chars": 20,
    # 块字数达到该值时密度分记满
    "full_score_chars": 280,
    # 正文总字数低于该值判为短正文，把握程度封顶
    "short_text_chars": 200,
    "short_confidence": 0.5,
    # 正文全靠救回时把握程度封顶
    "rescued_confidence": 0.3,
}

_SITE_RULES = {}
_LAST_GOOD = {}


class _Splitter(HTMLParser):
    """把 HTML 切成块级元素，文本记到最内层块，链接文字单独计数。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._elements = []  # (tag, class/id) 所有未闭合元素
        self._open = []      # 未闭合块级元素对应的 block
        self._skip = 0
        self._link = 0
        self._loose = None

    def _new_block(self, tag):
        block = {
            "index": len(self.blocks),
            "tag": tag,
            "attrs": " ".join(a for _, a in self._elements if a).lower(),
            "parts": [],
            "link_chars": 0,
        }
        self.blocks.append(block)
        return block

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag in _VOID_TAGS:
            return
        attr = " ".join(str(v) for k, v in attrs if k in ("class", "id") and v)
        self._elements.append((tag, attr))
        if tag == "a":
            self._link += 1
        if tag in _BLOCK_TAGS:
            self._open.append(self._new_block(tag))
            self._loose = None

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if tag in _VOID_TAGS:
            return
        for i in range(len(self._elements) - 1, -1, -1):
            if self._elements[i][0] == tag:
                del self._elements[i:]
                break
        if tag == "a" and self._link:
            self._link -= 1
        if tag in _BLOCK_TAGS:
            for i in range(len(self._open) - 1, -1, -1):
                if self._open[i]["tag"] == tag:
                    del self._open[i:]
                    break
            self._loose = None

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._open:
            block = self._open[-1]
        else:
            if self._loose is None:
                self._loose = self._new_block("")
            block = self._loose
        block["parts"].append(text)
        if self._link:
            block["link_chars"] += len(text)


def _classify(block, rules):
    text = " ".join(block["parts"]).strip()
    chars = len(text)
    link_density = block["link_chars"] / chars if chars else 0.0
    out = {
        "index": block["index"], "tag": block["tag"], "text": text,
        "chars": chars, "link_density": round(link_density, 3),
        "score": 0.0, "kept": False, "reason": "",
    }
    if not chars:
        out["reason"] = "empty"
        return out
    if block["tag"] in rules["noise_tags"]:
        out["reason"] = "noise-tag"
        return out
    if any(p in block["attrs"] for p in rules["noise_patterns"]):
        out["reason"] = "noise-pattern"
        return out
    if link_density > rules["link_density_max"]:
        out["reason"] = "link-density"
        return out
    punct = sum(1 for c in text if c in _PUNCT)
    density = min(1.0, chars / rules["full_score_chars"])
    punct_score = min(1.0, punct / max(1.0, chars / 40.0))
    out["score"] = round((0.7 * density + 0.3 * punct_score) * (1.0 - link_density), 3)
    if chars < rules["min_block_chars"]:
        out["reason"] = "too-short"  # 软噪声：没有其它正文时救回
        return out
    out["kept"] = True
    out["reason"] = "content"
    return out


def _extract(html, rules):
    splitter = _Splitter()
    splitter.feed(html or "")
    splitter.close()
    classified = [_classify(b, rules) for b in splitter.blocks]
    kept = [b for b in classified if b["kept"]]
    rescued = False
    if not kept:
        soft = [b for b in classified if b["reason"] == "too-short"]
        if soft:  # 短正文整段保留，把握程度压低
            rescued = True
            for b in soft:
                b["kept"] = True
                b["reason"] = "rescued-short"
            kept = soft
    chars = sum(b["chars"] for b in kept)
    confidence = sum(b["score"] * b["chars"] for b in kept) / chars if chars else 0.0
    if rescued:
        confidence = min(confidence, rules["rescued_confidence"])
    elif chars < rules["short_text_chars"]:
        confidence = min(confidence, rules["short_confidence"])
    return {
        "text": "\n".join(b["text"] for b in kept),
        "chars": chars,
        "blocks": classified,
        "noise": [b for b in classified if not b["kept"]],
        "confidence": round(confidence, 3),
        "fallback": False,
    }


def configure(site, **overrides):
    """按站点覆盖规则，改版只改配置。未知键直接报错。"""
    unknown = set(overrides) - set(DEFAULT_RULES)
    if unknown:
        raise ValueError("unknown rules: %s" % ", ".join(sorted(unknown)))
    merged = dict(_SITE_RULES.get(site, {}))
    merged.update(overrides)
    _SITE_RULES[site] = merged


def _rules(site, overrides):
    rules = dict(DEFAULT_RULES)
    if site:
        rules.update(_SITE_RULES.get(site, {}))
    if overrides:
        rules.update(overrides)
    return rules


def blocks(html, site=None, rules=None):
    """切分并分类所有块：kept 标出正文块，reason 说明保留/剔除原因。"""
    return _extract(html, _rules(site, rules))["blocks"]


def extract(html, site=None, rules=None, previous=None, url=None):
    """抽取正文。失败（异常或正文为空）时保留上一次结果并标 fallback。"""
    key = (site, url) if url is not None else None
    try:
        result = _extract(html, _rules(site, rules))
        if not result["text"]:
            raise ValueError("empty content")
    except Exception as exc:
        prev = previous
        if prev is None and key is not None:
            prev = _LAST_GOOD.get(key)
        if prev is not None:
            out = dict(prev)
            out["fallback"] = True
            out["error"] = str(exc)
            return out
        return {"text": "", "chars": 0, "blocks": [], "noise": [],
                "confidence": 0.0, "fallback": False, "error": str(exc)}
    if key is not None:
        _LAST_GOOD[key] = result
    return result


def diff(old, new):
    """对比两次正文（extract 结果或纯文本），返回 unified diff 行。"""
    old_text = old.get("text", "") if isinstance(old, dict) else str(old)
    new_text = new.get("text", "") if isinstance(new, dict) else str(new)
    return list(difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile="previous", tofile="current", lineterm=""))
