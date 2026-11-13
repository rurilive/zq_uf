"""编码和换行规范化，当前整文件读入再替换。"""


def normalize(path, encoding="utf-8"):
    """按给定编码解码，CRLF换LF，其他不动。"""
    with open(path, "rb") as handle:
        raw = handle.read()
    text = raw.decode(encoding)
    text = text.replace("\r\n", "\n")
    out = path + ".norm"
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text)
    return {"text": text, "out": out, "lines": text.count("\n")}
