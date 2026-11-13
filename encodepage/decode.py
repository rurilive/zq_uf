"""网页编码解码的最小实现。"""


def decode(body, headers):
    content_type = headers.get("content-type", "")
    charset = "utf-8"
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1]
    return body.decode(charset, errors="strict")
