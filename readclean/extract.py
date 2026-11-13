"""网页正文抽取的最小实现。"""
import re

_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)


def extract_title(html):
    match = _TITLE.search(html)
    return match.group(1).strip() if match else ""


def extract(html, rules=None):
    text = _TAG.sub("\n", html)
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    return {"title": extract_title(html), "paragraphs": paragraphs, "confidence": 1.0}
