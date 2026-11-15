"""采集页面正文抽取，当前把标签去掉就返回。"""
import re

_TAG = re.compile(r"<[^>]+>")


def _text(html):
    return " ".join(_TAG.sub(" ", html or "").split())


def extract(html):
    """整页文本原样返回，导航和推荐位都算正文。"""
    return {"text": _text(html), "blocks": 1, "noise": [], "confidence": 1.0}


def blocks(html):
    """整页当成一个块。"""
    return [{"index": 0, "text": _text(html), "score": 1.0}]
