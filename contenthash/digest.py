"""稳定正文指纹：剔除模板噪声，按段落分块，取稳定块并集哈希。

仅依赖 Python 标准库（html.parser / hashlib / re / unicodedata / dataclasses）。
"""
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

# 相似度口径：稳定块集合的 Jaccard 重合率。
DEFAULT_THRESHOLD = 0.8
# 短正文（稳定块不超过该数）降低阈值，避免少量差异即判新内容。
SHORT_THRESHOLD = 0.6
SHORT_BLOCK_LIMIT = 4
# 少于该块数视为证据不足，不判近重复，只给低置信度参考。
MIN_EVIDENCE_BLOCKS = 2

_NOISE_TAGS = frozenset({
    "script", "style", "nav", "header", "footer", "aside",
    "noscript", "template",
})
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "li", "td", "th", "tr",
    "blockquote", "pre", "dd", "dt", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6",
})
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
_AD_KEYWORDS = frozenset({
    "ad", "ads", "advert", "adverts", "advertisement", "banner",
    "sponsor", "sponsored", "promo", "promotion", "googleads",
})
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def normalize_text(text):
    """空白与标点归一化：NFKC 后剔除所有 Unicode 标点，空白压成单空格。"""
    text = unicodedata.normalize("NFKC", text)
    kept = []
    for ch in text:
        category = unicodedata.category(ch)
        # 标点替换为空格而非删除，避免词与词（含中英文边界）被粘连。
        if category.startswith("P") or category.startswith("Z") \
                or ch in " \t\n\r\f\v":
            kept.append(" ")
        else:
            kept.append(ch)
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def _is_ad_marker(attrs):
    """class/id 中含广告标识（如 class="ad-banner"、id="googleads"）即视为广告块。"""
    for name, value in attrs:
        if name not in ("class", "id") or not value:
            continue
        tokens = {token for token in _TOKEN_SPLIT.split(value.lower()) if token}
        if tokens & _AD_KEYWORDS:
            return True
        if any(token.startswith(("ad-", "ad_", "ads-")) for token in tokens):
            return True
    return False


class _BlockExtractor(HTMLParser):
    """解析 HTML，跳过噪声块，按块级元素收集归一化后的正文段落。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._noise_depth = None
        self._block_stack = []
        self._loose_parts = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        if self._noise_depth is not None:
            return
        if tag in _NOISE_TAGS or _is_ad_marker(attrs):
            self._noise_depth = self._depth
            return
        if tag == "br":
            self._emit(" ")
        elif tag in _BLOCK_TAGS:
            self._block_stack.append([])

    def handle_startendtag(self, tag, attrs):
        if tag not in _VOID_TAGS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)
        elif tag == "br":
            self._emit(" ")

    def handle_endtag(self, tag):
        if self._noise_depth is not None:
            self._depth -= 1
            if self._depth < self._noise_depth:
                self._noise_depth = None
            return
        self._depth -= 1
        if tag in _BLOCK_TAGS and self._block_stack:
            normalized = normalize_text("".join(self._block_stack.pop()))
            if normalized:
                self.blocks.append(normalized)

    def handle_data(self, data):
        if self._noise_depth is not None:
            return
        self._emit(data)

    def _emit(self, data):
        if self._block_stack:
            self._block_stack[-1].append(data)
        else:
            self._loose_parts.append(data)

    def finish(self):
        loose = normalize_text("".join(self._loose_parts))
        if loose:
            self.blocks.append(loose)
        while self._block_stack:
            normalized = normalize_text("".join(self._block_stack.pop(0)))
            if normalized:
                self.blocks.append(normalized)
        # 稳定块集合：重复段落去重，保留首次出现顺序。
        unique = []
        seen = set()
        for block in self.blocks:
            if block not in seen:
                seen.add(block)
                unique.append(block)
        return unique


def extract_blocks(html):
    """剔除噪声后返回归一化的正文块列表（去重、保序）。"""
    parser = _BlockExtractor()
    parser.feed(html)
    return parser.finish()


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Fingerprint:
    """正文指纹结果。相等性比较包含 raw_hash，故原始页面不同则对象不同。"""

    hash: str
    raw_hash: str
    blocks: tuple
    block_hashes: tuple
    block_count: int
    short: bool
    confidence: float

    def __str__(self):
        return self.hash


@dataclass
class Similarity:
    """近重复判定结果，布尔值即 near_duplicate，可直接用于断言。"""

    score: float
    matched: int
    block_count_a: int
    block_count_b: int
    threshold: float
    short: bool
    confidence: float
    near_duplicate: bool
    reason: str

    def __bool__(self):
        return self.near_duplicate


def fingerprint(html):
    """对正文稳定块集合取并集指纹，同时保留整页原始哈希与块信息。"""
    blocks = extract_blocks(html)
    block_hashes = tuple(_sha256(block) for block in blocks)
    union_hash = _sha256("\n".join(sorted(set(block_hashes))))
    count = len(block_hashes)
    # 以短正文块上限为满置信基准，块越少置信度越低。
    confidence = round(min(1.0, count / float(SHORT_BLOCK_LIMIT)), 3)
    return Fingerprint(
        hash=union_hash,
        raw_hash=_sha256(html),
        blocks=tuple(blocks),
        block_hashes=block_hashes,
        block_count=count,
        short=count <= SHORT_BLOCK_LIMIT,
        confidence=confidence,
    )


def similar(a, b, threshold=DEFAULT_THRESHOLD):
    """按稳定块集合的 Jaccard 重合率判近重复，短正文自动降阈。"""
    fp_a = a if isinstance(a, Fingerprint) else fingerprint(a)
    fp_b = b if isinstance(b, Fingerprint) else fingerprint(b)
    set_a = set(fp_a.block_hashes)
    set_b = set(fp_b.block_hashes)
    union = set_a | set_b
    matched = len(set_a & set_b)
    score = round(matched / len(union), 3) if union else 0.0

    evidence = min(len(set_a), len(set_b))
    short = evidence <= SHORT_BLOCK_LIMIT
    active_threshold = SHORT_THRESHOLD if short else threshold
    confidence = round(min(1.0, evidence / float(SHORT_BLOCK_LIMIT)), 3)

    if evidence < MIN_EVIDENCE_BLOCKS:
        decision = False
        reason = (
            f"稳定块仅 {evidence} 个（少于 {MIN_EVIDENCE_BLOCKS} 个），证据不足，"
            f"重合率 {score:.3f}，不判近重复"
        )
    elif score >= active_threshold:
        decision = True
        scope = "短正文降阈" if short else "常规阈值"
        reason = (
            f"重合 {matched} 块，重合率 {score:.3f} >= 阈值 {active_threshold:.2f}"
            f"（{scope}），判近重复"
        )
    else:
        decision = False
        scope = "短正文降阈" if short else "常规阈值"
        reason = (
            f"重合 {matched} 块，重合率 {score:.3f} < 阈值 {active_threshold:.2f}"
            f"（{scope}），判不同内容"
        )

    return Similarity(
        score=score,
        matched=matched,
        block_count_a=len(set_a),
        block_count_b=len(set_b),
        threshold=active_threshold,
        short=short,
        confidence=confidence,
        near_duplicate=decision,
        reason=reason,
    )


__all__ = [
    "fingerprint", "similar", "extract_blocks", "normalize_text",
    "Fingerprint", "Similarity",
    "DEFAULT_THRESHOLD", "SHORT_THRESHOLD", "SHORT_BLOCK_LIMIT",
    "MIN_EVIDENCE_BLOCKS",
]
