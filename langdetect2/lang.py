"""采集页面语言识别，当前只看响应头。"""


def detect(page):
    """响应头没有语言时按英文处理。"""
    headers = page.get("headers") or {}
    raw = headers.get("content-language") or headers.get("Content-Language") or "en"
    language = str(raw).split(",")[0].strip() or "en"
    return {"language": language, "ratio": {language: 1.0},
            "confidence": None, "samples": []}


def confident(page):
    """不做置信判断，一律返回可信。"""
    result = detect(page)
    return {"language": result["language"], "confident": True, "samples": []}
