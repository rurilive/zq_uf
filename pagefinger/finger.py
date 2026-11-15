"""正文分块页面指纹与近重复判定（纯 Python 标准库）。

口径摘要（详见 README）：
- 先剔除 script/style 与广告块（规则可扩展），再提取文本；
- 按块级标签分段，过短的块视为不稳定块丢弃，稳定块各自取指纹；
- 块指纹基于空白/标点归一化文本，页面 hash 是剔除噪声后的精确指纹；
- 指纹带版本号，算法升级后可按 version 批量重算历史数据；
- 相似度取两侧块哈希的 Jaccard 重合率，阈值支持按站点配置，
  短页面自动降阈并单列置信度。
"""
import hashlib
import re
import unicodedata
from html.parser import HTMLParser

# 指纹版本号：算法变更时递增，旧指纹可凭 version 字段批量重算。
FINGERPRINT_VERSION = 1

# 页面 hash 与块 hash 的输入都带版本前缀，升级版本后新旧指纹不会碰撞。
_VERSION_PREFIX = "pagefinger-v%d\n" % FINGERPRINT_VERSION
_BLOCK_PREFIX = "pagefinger-v%d:block:" % FINGERPRINT_VERSION

# 块级标签：遇到即切分段落。
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "main", "header", "footer", "nav",
    "aside", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "table", "tr", "td", "th", "figure",
    "figcaption", "details", "summary", "dl", "dt", "dd", "form",
    "fieldset", "address", "hr",
})

# 整段剔除的非正文标签。
_DROP_TAGS = frozenset({"script", "style", "noscript", "template", "iframe",
                        "embed", "object"})

_WS_RE = re.compile(r"\s+")
# CJK 字符（统一表意文字、假名、谚文、兼容形、全角形等）旁的空白不承载语义，
# 归一化时删除；拉丁词之间的空格保留。
_CJK_RANGE = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uF900-\uFAFF\u3000-\u303f\uFF00-\uFFEF"
_CJK_WS_RE = re.compile(
    r"(?<=[" + _CJK_RANGE + r"])\s+|\s+(?=[" + _CJK_RANGE + r"])")

# 常见 CJK 标点归一化（NFKC 覆盖不到的部分）。
_PUNCT_MAP = str.maketrans({
    "。": ".", "，": ",", "、": ",", "；": ";", "：": ":",
    "？": "?", "！": "!", "…": "...", "—": "-", "·": ".",
    "（": "(", "）": ")", "【": "[", "】": "]",
    "《": "<", "》": ">", "〈": "<", "〉": ">",
    "「": '"', "」": '"', "『": '"', "』": '"',
    "“": '"', "”": '"', "‘": "'", "’": "'",
})

# 广告识别默认规则用到的特征。
_AD_TAGS = frozenset({"ins"})  # AdSense 等插入位
_AD_TOKENS = frozenset({
    "ad", "ads", "advert", "advertisement", "adsense", "adbox",
    "sponsored", "promo", "banner", "ggad",
})
_AD_SRC_MARKERS = (
    "doubleclick", "googlesyndication", "adsystem", "adserver",
    "googleads", "/ads/", "/ad/", "ad.",
)


def _default_ad_rule(tag, attrs):
    """默认广告块规则：标签、id/class 令牌、广告投放 src 命中即视为广告。"""
    info = {k.lower(): (v or "") for k, v in attrs}
    if tag in _AD_TAGS:
        return True
    id_class = (info.get("id", "") + " " + info.get("class", "")).lower()
    tokens = [t for t in re.split(r"[\s_\-]+", id_class) if t]
    if any(t in _AD_TOKENS for t in tokens):
        return True
    if "google_ads" in id_class or "google-ad" in id_class:
        return True
    src = (info.get("src", "") or info.get("data-src", "")).lower()
    if src and any(marker in src for marker in _AD_SRC_MARKERS):
        return True
    return False


# 扩展规则注册表：自定义 callable(tag, attrs) -> bool，任一命中即剔除。
_AD_RULES = [_default_ad_rule]


def register_ad_rule(rule):
    """注册自定义广告识别规则，可作为装饰器使用。

    rule 签名为 rule(tag: str, attrs: list[tuple[str, str]]) -> bool。
    """
    if rule not in _AD_RULES:
        _AD_RULES.append(rule)
    return rule


def unregister_ad_rule(rule):
    """移除此前注册的自定义广告规则。"""
    if rule in _AD_RULES:
        _AD_RULES.remove(rule)


class Config:
    """相似度与分块参数，可整体调整或按站点覆盖。"""

    def __init__(self, threshold=0.75, short_threshold=0.5,
                 short_blocks=4, short_chars=120,
                 min_block_chars=8, sample_chars=60, max_samples=5):
        # 普通页面判定相似的块重合率阈值。
        self.threshold = threshold
        # 短页面降阈后的阈值。
        self.short_threshold = short_threshold
        # 稳定块少于该数、或正文字符少于 short_chars，即视为短页面。
        self.short_blocks = short_blocks
        self.short_chars = short_chars
        # 归一化后短于该长度的块视为不稳定块（导航词、按钮文案等）。
        self.min_block_chars = min_block_chars
        # 判定结果中块样本的截断长度与条数。
        self.sample_chars = sample_chars
        self.max_samples = max_samples

    def replace(self, **overrides):
        clone = Config(
            threshold=self.threshold,
            short_threshold=self.short_threshold,
            short_blocks=self.short_blocks,
            short_chars=self.short_chars,
            min_block_chars=self.min_block_chars,
            sample_chars=self.sample_chars,
            max_samples=self.max_samples,
        )
        for key, value in overrides.items():
            setattr(clone, key, value)
        return clone


_CONFIG = Config()
_SITE_CONFIG = {}


def configure(**overrides):
    """调整全局默认参数（threshold/short_threshold/short_blocks 等）。"""
    global _CONFIG
    _CONFIG = _CONFIG.replace(**overrides)
    return _CONFIG


def configure_site(site, **overrides):
    """按站点覆盖阈值等参数，site 一般取域名。"""
    _SITE_CONFIG[site] = dict(_SITE_CONFIG.get(site, {}), **overrides)


def reset_config():
    """恢复默认配置、站点覆盖与自定义广告规则（测试隔离用）。"""
    global _CONFIG
    _CONFIG = Config()
    _SITE_CONFIG.clear()
    _AD_RULES[:] = [_default_ad_rule]


def _config_for(site):
    overrides = _SITE_CONFIG.get(site)
    return _CONFIG.replace(**overrides) if overrides else _CONFIG


def _normalize(text):
    """空白与标点归一化：NFKC + CJK 标点转半角 + CJK 旁空白删除 + 空白折叠。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_PUNCT_MAP)
    text = _CJK_WS_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def _hash_payload(text):
    """块指纹载荷：归一化、小写、剔除标点，抗排版与标点改动。

    先做 NFKC/标点转半角与小写并剔除标点，再删 CJK 旁空白，最后折叠
    其余空白，避免标点或全角空格被删除后在原位残留空格导致载荷不一致。
    """
    normalized = unicodedata.normalize("NFKC", text).translate(_PUNCT_MAP).lower()
    kept = [ch for ch in normalized
            if not unicodedata.category(ch).startswith("P")]
    text = _CJK_WS_RE.sub("", "".join(kept))
    return _WS_RE.sub(" ", text).strip()


def _block_hash(payload):
    return hashlib.sha256(
        (_BLOCK_PREFIX + payload).encode("utf-8")).hexdigest()


def _sample(text, cfg):
    if len(text) <= cfg.sample_chars:
        return text
    return text[:cfg.sample_chars] + "…"


class _BodyExtractor(HTMLParser):
    """提取剔除噪声后的正文块，纯标准库 HTMLParser 实现。"""

    def __init__(self, cfg):
        super().__init__(convert_charrefs=True)
        self._cfg = cfg
        self._skip_stack = []   # 正在跳过的 script/style/广告标签栈
        self._buf = ""          # 当前块的原始文本
        self._cur_tag = "div"
        self.page_parts = []    # 剔除噪声后的全部原始文本段（页面 hash 用）
        self.blocks = []        # 稳定块记录
        self.dropped_noise = 0  # 被剔除的噪声块数量

    def _is_ad(self, tag, attrs):
        return any(rule(tag, attrs) for rule in _AD_RULES)

    def handle_starttag(self, tag, attrs):
        if self._skip_stack:
            self._skip_stack.append(tag)
            return
        if tag in _DROP_TAGS or self._is_ad(tag, attrs):
            self._flush()
            self._skip_stack.append(tag)
            self.dropped_noise += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            self._cur_tag = tag

    def handle_startendtag(self, tag, attrs):
        if self._skip_stack:
            return
        if tag == "br" or tag == "hr":
            self._buf += " "
        elif tag == "img":
            info = {k.lower(): (v or "") for k, v in attrs}
            alt = info.get("alt", "").strip()
            if alt:
                self._buf += " " + alt + " "

    def handle_endtag(self, tag):
        if self._skip_stack:
            if self._skip_stack[-1] == tag:
                self._skip_stack.pop()
            elif tag in self._skip_stack:
                # 容错：中间标签缺失闭合时，退回到匹配层，避免后续正文被误剔。
                while self._skip_stack and self._skip_stack[-1] != tag:
                    self._skip_stack.pop()
                if self._skip_stack:
                    self._skip_stack.pop()
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if not self._skip_stack:
            self._buf += data

    def _flush(self):
        raw = self._buf
        self._buf = ""
        if not raw.strip():
            return
        # 页面精确指纹保留原始空白差异（只剔除噪声，不做归一化）。
        self.page_parts.append(raw)
        normalized = _normalize(raw)
        if len(normalized) < self._cfg.min_block_chars:
            return
        payload = _hash_payload(raw)
        self.blocks.append({
            "index": len(self.blocks),
            "tag": self._cur_tag,
            "text": _sample(normalized, self._cfg),
            "hash": _block_hash(payload),
            "chars": len(normalized),
        })

    def close(self):
        super().close()
        self._flush()


def fingerprint(html):
    """计算正文分块指纹。

    返回 dict：version / hash（剔除噪声后的精确指纹）/ blocks（稳定块数）/
    block_hashes（归一化块指纹，近重复比对用）/ block_samples（块样本）/
    short 与 confidence（短页面单独标注）/ stats。
    同页多次计算结果完全一致。
    """
    cfg = _CONFIG
    if html is None:
        html = ""
    elif isinstance(html, bytes):
        html = html.decode("utf-8", "ignore")

    parser = _BodyExtractor(cfg)
    parser.feed(html)
    parser.close()

    material = _VERSION_PREFIX + "\x1f".join(parser.page_parts)
    page_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()

    total_chars = sum(block["chars"] for block in parser.blocks)
    short = (len(parser.blocks) < cfg.short_blocks
             or total_chars < cfg.short_chars)
    confidence = round(
        0.5 * min(1.0, len(parser.blocks) / 6.0)
        + 0.5 * min(1.0, total_chars / 300.0), 4)

    return {
        "version": FINGERPRINT_VERSION,
        "hash": page_hash,
        "blocks": len(parser.blocks),
        "short": short,
        "confidence": confidence,
        "block_hashes": [block["hash"] for block in parser.blocks],
        "block_samples": [dict(block) for block in parser.blocks],
        "stats": {
            "chars": total_chars,
            "dropped_noise_blocks": parser.dropped_noise,
        },
    }


def _side_samples(fp, cfg, allowed=None):
    samples = []
    for block in fp["block_samples"]:
        if allowed is not None and block["hash"] not in allowed:
            continue
        samples.append(block["text"])
        if len(samples) >= cfg.max_samples:
            break
    return samples


def similar(a, b, site=None, threshold=None):
    """判定两个页面是否近重复。

    相似度为稳定块哈希集合的 Jaccard 重合率；threshold 可显式传入，
    否则取站点配置，站点缺省回退全局默认；任一侧为短页面时自动降阈。
    判定结果保留两侧块样本与命中块样本，并单列短页面标记和置信度。
    """
    fa = fingerprint(a)
    fb = fingerprint(b)
    cfg = _config_for(site)

    short_page = fa["short"] or fb["short"]
    if threshold is None:
        effective = cfg.short_threshold if short_page else cfg.threshold
    else:
        effective = threshold

    set_a = set(fa["block_hashes"])
    set_b = set(fb["block_hashes"])
    union = set_a | set_b
    matched = set_a & set_b
    score = round(len(matched) / len(union), 4) if union else 0.0
    is_similar = score >= effective

    return {
        "similar": is_similar,
        "score": score,
        "threshold": effective,
        "short_page": short_page,
        "confidence": round(min(fa["confidence"], fb["confidence"]), 4),
        "matched_blocks": len(matched),
        "block_counts": {"a": fa["blocks"], "b": fb["blocks"]},
        "short_sides": {"a": fa["short"], "b": fb["short"]},
        "samples": {
            "a": _side_samples(fa, cfg),
            "b": _side_samples(fb, cfg),
        },
        "matched_samples": {
            "a": _side_samples(fa, cfg, allowed=matched),
            "b": _side_samples(fb, cfg, allowed=matched),
        },
    }
