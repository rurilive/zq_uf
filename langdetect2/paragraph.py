"""段落级语言识别。

流程：统计段落内各书写脚本的字母数 ->
- 一对多脚本（latin/cyrillic）用常用词表区分具体语言；
- 中日共用 han 脚本时，用假名（hiragana/katakana）是否出现区分 ja/zh；
- 其余脚本与语言一一对应。
"""

from . import stopwords
from .scripts import (
    CYRILLIC, HANGUL, HAN, HIRAGANA, KATAKANA, LATIN, OTHER,
    SCRIPT_LANGUAGE, count_scripts,
)

# 日本常用汉字中的一批特有字（简体中文不使用或写法不同）
_JAPANESE_SPECIFIC_HAN = set("込働僕辻峠畑躾匂枠搾缶腺腫癒軌閑駅黒")


def detect_paragraph(text, declared_lang=None):
    """识别单个段落，返回段落结果 dict。

    权重按字母数计：同一段落混排多脚本时，各语言按脚本字母数分摊权重，
    从而把"段落内混排"也反映到占比里。
    declared_lang 是该段落 HTML 声明的语言，仅在正文证据无法落语言时兜底。
    """
    counts = count_scripts(text)
    total = sum(counts.values())
    if total == 0:
        return None

    tokens = stopwords.tokenize(text)
    latin_scores = stopwords.latin_scores(tokens)
    cyr_scores = stopwords.cyrillic_scores(tokens)

    weights = {}          # 语言 -> 字母权重
    reasons = {}          # 语言 -> 判定依据
    strong_weight = 0     # 高把握语言承担的权重
    has_kana = counts.get(HIRAGANA, 0) + counts.get(KATAKANA, 0) > 0

    for script, n in counts.items():
        if script in (HIRAGANA, KATAKANA):
            lang, reason, strong = "ja", "kana", True
        elif script == HANGUL:
            lang, reason, strong = "ko", "hangul", True
        elif script == HAN:
            if has_kana:
                lang, reason, strong = "ja", "han-with-kana", True
            elif any(ch in _JAPANESE_SPECIFIC_HAN for ch in text):
                # 日文特有的汉字给出 ja，但无假名佐证，保留中等证据
                lang, reason, strong = "ja", "japanese-specific-kanji", False
            else:
                # 无假名的汉字块按中文处理；han 本身是确定性脚本
                lang, reason, strong = "zh", "han-no-kana", True
        elif script == LATIN:
            lang, reason, strong = _latin_choice(latin_scores)
        elif script == CYRILLIC:
            lang, reason, strong = _cyrillic_choice(cyr_scores)
        elif script == OTHER:
            lang, reason, strong = "unknown", "unmapped-script", False
        else:
            lang = SCRIPT_LANGUAGE[script]
            reason, strong = "unique-script", True

        weights[lang] = weights.get(lang, 0) + n
        reasons.setdefault(lang, reason)
        if strong:
            strong_weight += n

    # 归一化
    weights = {lang: round(n / total, 4) for lang, n in weights.items()}
    language = max(weights, key=weights.get)
    confidence = "high" if strong_weight / total >= 0.8 else (
        "low" if strong_weight / total < 0.4 else "medium")

    # 正文无法落到受支持语言（如拉丁短句没命中常用词）时，声明可兜底；
    # 声明只作弱证据，段落把握不超过 medium
    if language == "unknown" and declared_lang:
        weights = {declared_lang: weights.get("unknown", 1.0)}
        language = declared_lang
        reasons[declared_lang] = "declaration-fallback"
        confidence = "medium" if total >= 8 else "low"
        strong_weight = 0.0

    return {
        "language": language,
        "confidence": confidence,
        "weights": weights,
        "reasons": reasons,
        "strong_weight": round(strong_weight / total, 4),
        "letters": total,
        "text": text,
    }


def _latin_choice(scores):
    if not scores:
        return "unknown", "latin-no-stopword", False
    ranked = list(scores.items())
    best_lang, best_n = ranked[0]
    if best_n >= 2 or (len(ranked) == 1 and best_n >= 1):
        return best_lang, "stopword-strong", True
    if len(ranked) > 1 and best_n == ranked[1][1]:
        return best_lang, "stopword-tie", False
    return best_lang, "stopword-weak", False


def _cyrillic_choice(scores):
    if not scores:
        return "ru", "cyrillic-fallback", False
    best_lang, best_n = list(scores.items())[0]
    if best_n >= 2:
        return best_lang, "stopword-strong", True
    return best_lang, "stopword-weak", False
