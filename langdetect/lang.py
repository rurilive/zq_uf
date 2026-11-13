"""页面语言识别，当前只看有没有CJK字符。"""


def detect(text):
    """有汉字算中文，其余一律英文，置信度写死。"""
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            return {"lang": "zh", "confidence": 1.0}
    return {"lang": "en", "confidence": 1.0}
