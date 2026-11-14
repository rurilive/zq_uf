"""响应类型分派，当前按后缀。"""


def dispatch(response):
    """只看 URL 后缀。"""
    url = (response.get("url") or "").lower()
    if url.endswith(".json"):
        return "json"
    if url.endswith(".xml"):
        return "xml"
    return "html"


def parse(response):
    """统一按文本返回。"""
    return response.get("body") or ""
