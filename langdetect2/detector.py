"""整页语言识别编排。

分层证据：
1. 响应头 / HTML 声明（先看，但只作声明，与正文交叉验证，不单独定案）
2. 正文按段落做字符脚本归类 + 常用词打分（落到段落）
输出主语言、各语言占比、把握程度、原文样本，供入库分流。
"""

from urllib.parse import urlparse

from . import declaration as decl
from .paragraph import detect_paragraph

SAMPLE_LIMIT = 200          # 单条样本保留的最大字符数
SAMPLES_PER_LANG = 2        # 每种语言最多保留几条样本

# 页面级阈值
BODY_MIN_LETTERS = 15          # 低于该字母量视为证据不足
STRONG_RATIO_HIGH = 0.85
STRONG_RATIO_MEDIUM = 0.6
MARGIN_MIXED = 0.15            # 次语言占比超过该值视为混排


def site_of(page):
    """从 url 提取站点（host:port 去掉大小写），没有 url 返回 None。"""
    url = page.get("url") or ""
    if not url:
        return None
    host = urlparse(url if "://" in url else "http://" + url).netloc
    return host.lower() or None


def split_paragraphs(text):
    """纯文本分段：优先按空行，退化为按换行，再退化为整段。"""
    if not text:
        return []
    chunks = [p.strip() for p in text.split("\n\n")]
    if len(chunks) <= 1:
        chunks = [p.strip() for p in text.splitlines()]
    return [p for p in chunks if p]


def _extract_blocks(page):
    """返回 (段落输入 [{text, declared_lang}], 声明语言列表)。"""
    headers = page.get("headers") or {}
    header_langs = decl.parse_header_langs(headers)
    html = page.get("html")

    if html is not None:
        blocks, html_langs = decl.parse_html(html)
        if blocks:
            declared = _merge_langs(header_langs, html_langs)
            return blocks, declared
        # 空 HTML 骨架：声明仍然有效
        return [], header_langs

    if "paragraphs" in page and page["paragraphs"]:
        blocks = [{"text": p, "lang": None} for p in page["paragraphs"]]
        return blocks, header_langs

    blocks = [{"text": p, "lang": None}
              for p in split_paragraphs(page.get("text") or "")]
    return blocks, header_langs


def _merge_langs(primary, secondary):
    out = list(primary)
    for lang in secondary:
        if lang not in out:
            out.append(lang)
    return out


def detect(page):
    """识别一个页面，返回完整结果 dict。"""
    blocks, declared = _extract_blocks(page)

    paragraphs = []
    for block in blocks:
        result = detect_paragraph(block["text"],
                                  declared_lang=block.get("lang"))
        if result is None:
            continue
        result["declared_lang"] = block.get("lang")
        paragraphs.append(result)

    ratio, strong_share, total_letters = _aggregate(paragraphs)
    language, confidence, reason, conflict = _judge(
        ratio, strong_share, total_letters, declared)
    samples = _collect_samples(paragraphs)
    is_multilingual = len([r for r in ratio.values() if r >= MARGIN_MIXED]) > 1

    return {
        "site": site_of(page),
        "url": page.get("url"),
        "language": language,
        "ratio": ratio,
        "confidence": confidence,
        "confidence_reason": reason,
        "declared_languages": declared,
        "declaration_conflict": conflict,
        "multilingual": is_multilingual,
        "samples": samples,
        "paragraphs": [
            {"language": p["language"], "confidence": p["confidence"],
             "ratio": p["weights"], "letters": p["letters"],
             "declared_lang": p["declared_lang"],
             "sample": p["text"][:SAMPLE_LIMIT]}
            for p in paragraphs
        ],
    }


def _aggregate(paragraphs):
    """按字母量加权汇总各语言占比。"""
    total = sum(p["letters"] for p in paragraphs)
    if total == 0:
        return {}, 0.0, 0
    lang_letters = {}
    strong_letters = 0
    for p in paragraphs:
        for lang, share in p["weights"].items():
            lang_letters[lang] = lang_letters.get(lang, 0) \
                + share * p["letters"]
        strong_letters += p["strong_weight"] * p["letters"]
    ratio = {lang: round(n / total, 4)
             for lang, n in sorted(lang_letters.items(),
                                   key=lambda kv: kv[1], reverse=True)}
    # 修正四舍五入误差，保证占比之和为 1
    drift = round(1.0 - sum(ratio.values()), 4)
    if ratio and drift:
        top = next(iter(ratio))
        ratio[top] = round(ratio[top] + drift, 4)
    return ratio, round(strong_letters / total, 4), total


def _judge(ratio, strong_share, total_letters, declared):
    """决定主语言、把握程度与原因。返回 (language, confidence, reason, conflict)。"""
    if not ratio:
        if declared:
            return declared[0], "medium", "declaration-only", False
        return "unknown", "low", "no-text-content", False

    dominant = next(iter(ratio))
    dominant_ratio = ratio[dominant]
    second_ratio = list(ratio.values())[1] if len(ratio) > 1 else 0.0
    declared_set = set(declared)
    conflict = bool(declared_set) and dominant not in declared_set
    # 声明里出现正文识别出的非主语言，属于声明与正文部分一致而非冲突
    if conflict and declared_set & set(ratio):
        conflict = False

    if dominant == "unknown":
        # 脚本无法映射到受支持语言：声明可兜底
        if declared:
            return declared[0], "low", "declared-on-unresolved-script", True
        return "unknown", "low", "unresolved-script", False

    if total_letters < BODY_MIN_LETTERS:
        if declared and dominant in declared_set:
            return dominant, "medium", "short-content-declaration-match", False
        if declared:
            return dominant, "low", "short-content-declaration-conflict", True
        return dominant, "low", "short-content", False

    if strong_share < STRONG_RATIO_MEDIUM:
        # 正文证据无法落到受支持语言
        confidence, reason = "low", "weak-evidence"
    elif dominant_ratio < STRONG_RATIO_MEDIUM:
        # 语言过于分散，没有主语言
        confidence, reason = "low", "no-dominant-language"
    elif conflict:
        # 声明与正文不一致：正文证据非常充分才给 medium，否则转人工
        confidence = "medium" if dominant_ratio >= STRONG_RATIO_HIGH else "low"
        reason = "declaration-conflict"
    elif second_ratio >= MARGIN_MIXED or dominant_ratio < STRONG_RATIO_HIGH:
        # 混排页或主语言不占绝对优势：可分流但保留人工复核入口
        confidence, reason = "medium", "mixed-page"
    else:
        confidence, reason = "high", "dominant-script"
        if declared and dominant in declared_set:
            reason = "dominant-script-declared"
    return dominant, confidence, reason, conflict


def _collect_samples(paragraphs):
    """每种语言保留原文样本（截断但不翻译、不改写），供人工复核。"""
    per_lang = {}
    for p in paragraphs:
        lang = p["language"]
        bucket = per_lang.setdefault(lang, [])
        if len(bucket) >= SAMPLES_PER_LANG:
            continue
        text = p["text"]
        bucket.append({
            "language": lang,
            "text": text[:SAMPLE_LIMIT],
            "truncated": len(text) > SAMPLE_LIMIT,
            "confidence": p["confidence"],
        })
    samples = []
    for lang in sorted(per_lang):
        samples.extend(per_lang[lang])
    return samples


def confident(page):
    """入库分流视图：high 直接入库，medium 转人工，low 不直接入库。"""
    result = detect(page)
    level = result["confidence"]
    return {
        "language": result["language"],
        "confident": level == "high",
        "confidence": level,
        "indexable": level == "high",
        "needs_review": level != "high",
        "samples": result["samples"],
        "site": result["site"],
    }
