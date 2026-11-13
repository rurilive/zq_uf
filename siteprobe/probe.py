"""单页探测的最小实现。"""


def probe(url, fetch):
    response = fetch(url) or {}
    body = response.get("body", "")
    headers = response.get("headers", {})
    return {
        "url": url,
        "status": response.get("status", 0),
        "bytes": len(body),
        "content_type": headers.get("content-type", ""),
    }
