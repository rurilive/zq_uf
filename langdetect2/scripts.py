"""按 Unicode 字符范围判断书写脚本（script）。

只识别字母类字符，标点/数字/空白返回 None，不参与语言权重。
"""

import unicodedata

HIRAGANA = "hiragana"
KATAKANA = "katakana"
HANGUL = "hangul"
HAN = "han"
LATIN = "latin"
CYRILLIC = "cyrillic"
ARABIC = "arabic"
HEBREW = "hebrew"
GREEK = "greek"
THAI = "thai"
DEVANAGARI = "devanagari"
BENGALI = "bengali"
TAMIL = "tamil"
ARMENIAN = "armenian"
GEORGIAN = "georgian"
OTHER = "other"

# 脚本可直接确定的语言（一对多脚本如 latin/cyrillic/han 不在此列）
SCRIPT_LANGUAGE = {
    HIRAGANA: "ja",
    KATAKANA: "ja",
    HANGUL: "ko",
    ARABIC: "ar",
    HEBREW: "he",
    GREEK: "el",
    THAI: "th",
    DEVANAGARI: "hi",
    BENGALI: "bn",
    TAMIL: "ta",
    ARMENIAN: "hy",
    GEORGIAN: "ka",
    # 西里尔脚本缺省回退俄语，常用词可区分 ru/uk
    CYRILLIC: "ru",
}


def script_of(ch):
    """返回单个字符的书写脚本；非字母字符返回 None。"""
    cp = ord(ch)
    # 长音符号 U+30FC 是日语片假名的一部分，优先判定
    if cp == 0x30FC:
        return KATAKANA
    if 0x3040 <= cp <= 0x309F:
        return HIRAGANA
    if 0x30A0 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF or 0xFF66 <= cp <= 0xFF9F:
        return KATAKANA
    if (0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF
            or 0x3130 <= cp <= 0x318F or 0xA960 <= cp <= 0xA97F):
        return HANGUL
    if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
            or 0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x2A6DF):
        return HAN
    if (0x0041 <= cp <= 0x005A or 0x0061 <= cp <= 0x007A
            or 0x00C0 <= cp <= 0x024F or 0x1E00 <= cp <= 0x1EFF):
        return LATIN
    if 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F:
        return CYRILLIC
    if (0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F
            or 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF):
        return ARABIC
    if 0x0590 <= cp <= 0x05FF:
        return HEBREW
    if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
        return GREEK
    if 0x0E00 <= cp <= 0x0E7F:
        return THAI
    if 0x0900 <= cp <= 0x097F:
        return DEVANAGARI
    if 0x0980 <= cp <= 0x09FF:
        return BENGALI
    if 0x0B80 <= cp <= 0x0BFF:
        return TAMIL
    if 0x0530 <= cp <= 0x058F:
        return ARMENIAN
    if 0x10A0 <= cp <= 0x10FF:
        return GEORGIAN
    if not unicodedata.category(ch).startswith("L"):
        return None
    return OTHER


def count_scripts(text):
    """统计文本中各脚本的字母数量。"""
    counts = {}
    for ch in text:
        script = script_of(ch)
        if script is not None:
            counts[script] = counts.get(script, 0) + 1
    return counts
