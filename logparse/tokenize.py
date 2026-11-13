"""引号与转义感知的切分器。"""

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class TokenizeError(ValueError):
    """行内容无法切分（如引号未闭合）。"""


def tokenize(line):
    """按空白切分；双引号内保留空白，反斜杠转义下一个字符。"""
    tokens = []
    buf = []
    in_quotes = False
    escaped = False
    started = False
    for ch in line:
        if escaped:
            buf.append(_ESCAPES.get(ch, ch))
            escaped = False
            started = True
        elif ch == "\\":
            escaped = True
            started = True
        elif ch == '"':
            in_quotes = not in_quotes
            started = True
        elif ch.isspace() and not in_quotes:
            if started:
                tokens.append("".join(buf))
                buf = []
                started = False
        else:
            buf.append(ch)
            started = True
    if escaped:
        buf.append("\\")
    if in_quotes:
        raise TokenizeError("unterminated quote")
    if started:
        tokens.append("".join(buf))
    return tokens
