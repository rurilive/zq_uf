"""多语言页面识别：字符区间 + 常见词/停用词打分，支持中英日韩法德。"""

import re

SUPPORTED_LANGS = ("zh", "ja", "ko", "en", "fr", "de")

DEFAULT_CONFIG = {
    # 置信度低于阈值时标记 low_confidence，结果保留不丢弃，路由层可按需调整
    "min_confidence": 0.6,
    # 短文本：长度阈值、置信度缩放、放宽后的判定阈值
    "short_text_length": 20,
    "short_text_confidence_scale": 0.6,
    "short_text_min_confidence": 0.35,
    # 打分权重：单次字符命中 / 单次词命中
    "char_weight": 1.0,
    "word_weight": 3.0,
}

_LATIN_WORD_RE = re.compile(r"[a-zßà-öø-ÿ]+")

_LATIN_KEYWORDS = {
    "en": {
        "the", "and", "is", "are", "was", "were", "of", "to", "in", "on",
        "for", "with", "a", "an", "this", "that", "it", "as", "at", "by",
        "from", "be", "been", "has", "have", "not", "but", "or", "we",
        "you", "very", "good", "price", "sale", "keyboard", "shipping",
        "free", "now", "new",
    },
    "fr": {
        "le", "la", "les", "des", "de", "du", "et", "est", "un", "une",
        "pour", "avec", "sur", "dans", "pas", "ce", "cette", "il", "elle",
        "nous", "vous", "au", "aux", "qui", "que", "très", "tres", "bon",
        "prix", "clavier", "mécanique", "mecanique", "livraison", "gratuit",
        "bonjour", "merci", "salut",
    },
    "de": {
        "der", "die", "das", "und", "ist", "ein", "eine", "einen", "mit",
        "für", "fuer", "auf", "nicht", "den", "dem", "ich", "sie", "wir",
        "er", "es", "zu", "im", "auch", "sehr", "gut", "preis", "tastatur",
        "versand", "kostenlos", "maus",
    },
}

_CJK_KEYWORDS = {
    "zh": (
        "的", "了", "是", "在", "我", "有", "和", "就", "不", "都", "也",
        "你", "他", "她", "这", "那", "个", "们", "要", "会", "好", "很",
        "元", "买", "商品", "价格", "包邮", "键盘", "到家",
    ),
    "ja": (
        "の", "は", "が", "を", "に", "へ", "と", "で", "も", "です",
        "ます", "する", "ある", "いる", "この", "その", "これ", "それ",
        "価格", "安い", "静か",
    ),
    "ko": (
        "은", "는", "이", "가", "을", "를", "에", "의", "도", "로",
        "와", "과", "입니다", "습니다", "해요", "좋아요", "배송", "가격",
        "저렴",
    ),
}

_FRENCH_ACCENTS = frozenset("àâçéèêëîïôùûÿ")
_GERMAN_ACCENTS = frozenset("äöüß")


def _char_points(char):
    """单个字符对各语言的证据分。"""
    code = ord(char)
    if 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF:
        return {"ja": 4.0}
    if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF:
        return {"ko": 4.0}
    if 0x4E00 <= code <= 0x9FFF:
        return {"zh": 1.0, "ja": 0.4}
    if "a" <= char <= "z" or "A" <= char <= "Z":
        return {"en": 0.3, "fr": 0.3, "de": 0.3}
    lower = char.lower()
    if lower in _FRENCH_ACCENTS:
        return {"fr": 2.0}
    if lower in _GERMAN_ACCENTS:
        points = {"de": 2.0}
        if lower == "ü":
            points["fr"] = 0.5
        return points
    return {}


def _score_text(text, config):
    scores = {lang: 0.0 for lang in SUPPORTED_LANGS}
    char_weight = config["char_weight"]
    word_weight = config["word_weight"]
    for char in text:
        for lang, points in _char_points(char).items():
            scores[lang] += points * char_weight
    lowered = text.lower()
    for word in _LATIN_WORD_RE.findall(lowered):
        for lang, keywords in _LATIN_KEYWORDS.items():
            if word in keywords:
                scores[lang] += word_weight
    for lang, keywords in _CJK_KEYWORDS.items():
        for keyword in keywords:
            count = text.count(keyword)
            if count:
                scores[lang] += word_weight * count
    return scores


def _best_guess(scores):
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_lang, best = ranked[0]
    second = ranked[1][1]
    if best <= 0:
        return "unknown", 0.0
    return best_lang, best / (best + second) if second > 0 else 1.0


def _detect_segment(text, config):
    lang, confidence = _best_guess(_score_text(text, config))
    short = len(text) < config["short_text_length"]
    if short:
        confidence *= config["short_text_confidence_scale"]
    threshold = config["short_text_min_confidence"] if short else config["min_confidence"]
    confidence = round(confidence, 4)
    return {
        "lang": lang,
        "confidence": confidence,
        "low_confidence": confidence < threshold,
        "text": text,
    }


def detect(text, config=None):
    """识别文本语言，返回语言、置信度、逐段结果和语言分布。

    config 可覆盖 DEFAULT_CONFIG 中的阈值与权重，便于路由层调整。
    低置信度结果标记 low_confidence=True，不丢弃。
    """
    merged = dict(DEFAULT_CONFIG)
    if config:
        merged.update(config)
    paragraphs = [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]
    if not paragraphs:
        return {
            "lang": "unknown",
            "confidence": 0.0,
            "low_confidence": True,
            "mixed": False,
            "segments": [],
            "distribution": {},
        }
    segments = [_detect_segment(p, merged) for p in paragraphs]
    distribution = {}
    for segment in segments:
        entry = distribution.setdefault(
            segment["lang"], {"paragraphs": 0, "confidence": 0.0}
        )
        entry["paragraphs"] += 1
        entry["confidence"] += segment["confidence"]
    for entry in distribution.values():
        entry["confidence"] = round(entry["confidence"] / entry["paragraphs"], 4)
    dominant_lang, dominant = max(
        distribution.items(),
        key=lambda item: (item[1]["paragraphs"], item[1]["confidence"]),
    )
    dominant_low = all(
        segment["low_confidence"]
        for segment in segments
        if segment["lang"] == dominant_lang
    )
    return {
        "lang": dominant_lang,
        "confidence": dominant["confidence"],
        "low_confidence": dominant_low,
        "mixed": len(distribution) > 1,
        "segments": segments,
        "distribution": distribution,
    }
