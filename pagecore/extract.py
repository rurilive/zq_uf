"""按块打分的正文抽取。

把页面切成块（nav/article/section/div 等分节标签为界，p/span/a 不切块），
按文本密度、链接密度、标点比例给块打分，正文与噪声分开；
短正文保留并给低置信标记；规则可按站点覆盖、按版本回退。
"""
import math
from html.parser import HTMLParser

# 分节标签：遇到就切新块；p/span/a 等不切块，文本并入所属块
_SECTION_TAGS = frozenset({
    "nav", "article", "section", "aside", "header", "footer", "main",
    "div", "ul", "ol", "dl", "table", "form", "figure", "blockquote",
    "fieldset", "details",
})
_DROP_TAGS = frozenset({"script", "style", "noscript", "template"})
# 这些标签结束时补空格，避免相邻文本粘连
_SEPARATOR_TAGS = frozenset({
    "p", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
}) | _SECTION_TAGS
_VOID_TAGS = frozenset({"br", "img", "hr", "input", "meta", "link", "wbr"})

_PUNCT = set("，。！？；：、…—·“”‘’《》（）【】.,!?;:-()[]\"'")

# 默认规则：所有阈值都可按站点覆盖
DEFAULT_RULES = {
    "name": "default",
    "min_score": 6.0,             # 正文块最低分
    "max_link_density": 0.5,      # 正文块链接密度上限
    "rescue_min_text": 10,        # 段落补回的最短文本
    "rescue_max_link_density": 0.3,  # 段落补回的链接密度上限
    "rescue_min_punct": 0.02,     # 段落补回的标点比例下限
    "short_content_len": 100,     # 正文短于此长度给低置信标记
}

# 站点规则：值为版本列表，最后一版是当前版。
# 误抽时 extract(html, site=..., rules_version=旧版本号) 回退上一版。
SITE_RULES = {
    # 窄栏站点：每段一个 div，单段分低，调低阈值靠段落补回拼出全文
    "narrow.example": [
        {"name": "narrow-v1", "min_score": 6.0},
        {"name": "narrow-v2", "min_score": 2.5, "short_content_len": 60},
    ],
}


class _Frame:
    """一个分节元素的解析状态：已完成子块 + 当前累积的文本段。"""

    __slots__ = ("tag", "blocks", "cur_text", "cur_link", "cur_tags")

    def __init__(self, tag):
        self.tag = tag
        self.blocks = []    # 已完成的块，按文档顺序
        self.cur_text = []  # 当前文本段
        self.cur_link = []  # 当前文本段里 <a> 内的文本
        self.cur_tags = 0   # 当前文本段里的标签数（算文本密度用）

    def flush(self):
        text = "".join(self.cur_text)
        if text.split():
            self.blocks.append(_make_block(
                self.tag, text, "".join(self.cur_link), self.cur_tags))
        self.cur_text, self.cur_link, self.cur_tags = [], [], 0


class _BlockParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = [_Frame("root")]
        self.drop_depth = 0
        self.link_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth:
            return
        if tag in _SECTION_TAGS:
            self.stack.append(_Frame(tag))
            return
        if tag == "a":
            self.link_depth += 1
        if tag in ("br", "wbr"):
            self.stack[-1].cur_text.append(" ")
        elif tag not in _VOID_TAGS:
            self.stack[-1].cur_tags += 1

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "wbr") and not self.drop_depth:
            self.stack[-1].cur_text.append(" ")

    def handle_endtag(self, tag):
        if tag in _DROP_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth:
            return
        if tag == "a" and self.link_depth:
            self.link_depth -= 1
        if tag in _SEPARATOR_TAGS:
            self.stack[-1].cur_text.append(" ")
        if tag in _SECTION_TAGS and len(self.stack) > 1:
            frame = self.stack.pop()
            frame.flush()
            parent = self.stack[-1]
            parent.flush()  # 子块前的文本先成块，保持文档顺序
            parent.blocks.extend(frame.blocks)

    def handle_data(self, data):
        if self.drop_depth:
            return
        top = self.stack[-1]
        top.cur_text.append(data)
        if self.link_depth:
            top.cur_link.append(data)

    def finish(self):
        self.close()
        while len(self.stack) > 1:  # 未闭合的分节标签
            frame = self.stack.pop()
            frame.flush()
            parent = self.stack[-1]
            parent.flush()
            parent.blocks.extend(frame.blocks)
        root = self.stack[0]
        root.flush()
        return root.blocks


def _make_block(tag, raw_text, raw_link, tag_count):
    text = " ".join(raw_text.split())
    text_len = len(text)
    link_len = len(" ".join(raw_link.split()))
    punct = sum(1 for ch in text if ch in _PUNCT)
    return {
        "index": -1,
        "tag": tag,
        "text": text,
        "text_len": text_len,
        "link_density": round(link_len / text_len, 4) if text_len else 0.0,
        "punct_ratio": round(punct / text_len, 4) if text_len else 0.0,
        "text_density": round(text_len / (text_len + 10 * tag_count), 4)
        if text_len else 0.0,
        "score": 0.0,
        "is_content": False,
    }


def _score(block):
    """sqrt(文本长) × 文本密度 × (1-链接密度) × (1+标点比例)。"""
    if not block["text_len"]:
        return 0.0
    return round(
        math.sqrt(block["text_len"])
        * block["text_density"]
        * (1.0 - block["link_density"])
        * (1.0 + block["punct_ratio"]),
        4,
    )


def _resolve_rules(site, rules_version, config):
    rules = dict(DEFAULT_RULES)
    meta = {"site": site or "default", "version": 0, "name": DEFAULT_RULES["name"]}
    if site and site in SITE_RULES:
        versions = SITE_RULES[site]
        idx = len(versions) - 1 if rules_version is None else rules_version
        idx = max(0, min(idx, len(versions) - 1))
        rules.update(versions[idx])
        meta = {"site": site, "version": idx,
                "name": versions[idx].get("name", site)}
    if config:
        rules.update(config)
    return rules, meta


def _is_paragraph(block, rules):
    """段落补回标准：够长、链接少、有标点，像正文段落（窄栏/尾段）。"""
    return (block["text_len"] >= rules["rescue_min_text"]
            and block["link_density"] <= rules["rescue_max_link_density"]
            and block["punct_ratio"] >= rules["rescue_min_punct"])


def blocks(html, site=None, rules_version=None, config=None):
    """切块并打分，返回块列表（含分数和正文/噪声判定）。"""
    rules, _ = _resolve_rules(site, rules_version, config)
    parser = _BlockParser()
    parser.feed(html or "")
    blks = parser.finish()
    for i, b in enumerate(blks):
        b["index"] = i
        b["score"] = _score(b)
        b["is_content"] = (b["score"] >= rules["min_score"]
                           and b["link_density"] <= rules["max_link_density"])
    return blks


def extract(html, site=None, rules_version=None, config=None):
    """抽取正文，返回正文、块分数、噪声、置信度、命中规则和正文边界。"""
    rules, meta = _resolve_rules(site, rules_version, config)
    blks = blocks(html, site=site, rules_version=rules_version, config=config)
    scores = [b["score"] for b in blks]
    if not blks:
        return {"text": "", "blocks": 0, "noise": [], "confidence": 1.0,
                "low_confidence": False, "rule": meta, "boundaries": None,
                "scores": []}
    if len(blks) == 1:
        # 单块文档没有可比较的块，原样返回；短文本仍给低置信标记
        b = blks[0]
        return {"text": b["text"], "blocks": 1, "noise": [], "confidence": 1.0,
                "low_confidence": b["text_len"] < rules["short_content_len"],
                "rule": meta,
                "boundaries": {"start_block": 0, "end_block": 0,
                               "start": 0, "end": b["text_len"]},
                "scores": scores}

    content_idx = {i for i, b in enumerate(blks) if b["is_content"]}
    fallback = not content_idx
    if fallback:
        # 没有块过线：短正文/列表页，全量保留，低置信
        lo, hi = 0, len(blks) - 1
    else:
        # 以最高分块为锚，向两边扩展到连续正文块（长文分块不丢尾段）
        anchor = max(content_idx, key=lambda i: blks[i]["score"])
        lo = hi = anchor
        while lo - 1 in content_idx:
            lo -= 1
        while hi + 1 in content_idx:
            hi += 1
        # 段落补回：相邻低分但像段落的块（窄栏文章按段落补回）
        while lo > 0 and _is_paragraph(blks[lo - 1], rules):
            lo -= 1
        while hi < len(blks) - 1 and _is_paragraph(blks[hi + 1], rules):
            hi += 1

    selected = set(range(lo, hi + 1))
    text = "\n".join(blks[i]["text"] for i in sorted(selected))
    noise = [b["text"] for i, b in enumerate(blks) if i not in selected]
    content_len = sum(blks[i]["text_len"] for i in selected)
    total_len = sum(b["text_len"] for b in blks)

    if fallback:
        confidence = 0.2
    else:
        share = content_len / total_len if total_len else 0.0
        confidence = round(min(0.99, 0.5 + 0.5 * share), 2)
    low_confidence = fallback or content_len < rules["short_content_len"]

    # 正文边界：块下标范围 + 在全部块文本拼接中的字符偏移
    start = sum(blks[i]["text_len"] + 1 for i in range(lo))
    end = start + sum(blks[i]["text_len"] + 1 for i in range(lo, hi + 1)) - 1
    return {"text": text, "blocks": len(selected), "noise": noise,
            "confidence": confidence, "low_confidence": low_confidence,
            "rule": meta,
            "boundaries": {"start_block": lo, "end_block": hi,
                           "start": start, "end": end},
            "scores": scores}
