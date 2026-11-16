"""混淆还原（纯静态）。

所有变换都只停留在字符串层面，绝不执行待检测代码，还原结果交给规则引擎二次判定。
"""

import base64
import binascii
import re
import zlib

_SQ_CONCAT = re.compile(r"'((?:\\.|[^'\\])*)'\s*\.\s*'((?:\\.|[^'\\])*)'")
_DQ_CONCAT = re.compile(r'"((?:\\.|[^"\\])*)"\s*\.\s*"((?:\\.|[^"\\])*)"')
_CHR_CHAIN = re.compile(r"(?:chr\s*\(\s*\d+\s*\)\s*(?:\.\s*)?)+")
_DQ_STRING = re.compile(r'"((?:\\.|[^"\\])*)"')
_WRAPPER = re.compile(
    r"\b(base64_decode|gzinflate|gzuncompress|str_rot13|strrev|urldecode|hex2bin)"
    r"\s*\(\s*(['\"])((?:\\.|(?!\2).)*)\2\s*\)"
)
_B64_TOKEN = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_PRINTABLE = set(range(0x20, 0x7F)) | {9, 10, 13}
_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "0": "\0"}


def _printable_ratio(raw):
    if not raw:
        return 0.0
    good = sum(1 for b in raw if b in _PRINTABLE)
    return good / len(raw)


def fold_concatenation(text):
    """把 'ev'.'al' 这类相邻字符串字面量折叠成 'eval'。"""
    records = []
    for _ in range(10):
        before = text
        for pattern, quote in ((_SQ_CONCAT, "'"), (_DQ_CONCAT, '"')):
            while True:
                match = pattern.search(text)
                if not match:
                    break
                merged = match.group(1) + match.group(2)
                records.append(
                    {"technique": "string_concat", "from": match.group(0), "to": merged}
                )
                text = text[: match.start()] + quote + merged + quote + text[match.end() :]
        if text == before:
            break
    return text, records


def _replace_chr(match):
    nums = re.findall(r"chr\s*\(\s*(\d+)\s*\)", match.group(0))
    try:
        return "'" + "".join(chr(int(num)) for num in nums) + "'"
    except (ValueError, OverflowError):
        return match.group(0)


def replace_chr(text):
    """把 chr(101).chr(118) 链还原成普通字符串。"""
    records = []

    def _collect(match):
        replaced = _replace_chr(match)
        if replaced != match.group(0):
            records.append(
                {"technique": "chr_chain", "from": match.group(0).strip(), "to": replaced}
            )
        return replaced

    return _CHR_CHAIN.sub(_collect, text), records


def _decode_double_quoted(match):
    inner = match.group(1)
    if "\\" not in inner:
        return match.group(0)
    out, i, changed = [], 0, False
    while i < len(inner):
        char = inner[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        nxt = inner[i + 1] if i + 1 < len(inner) else ""
        if nxt == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", inner[i + 2 : i + 4]):
            out.append(chr(int(inner[i + 2 : i + 4], 16)))
            i += 4
            changed = True
            continue
        if nxt == "u" and i + 2 < len(inner) and inner[i + 2] == "{":
            end = inner.find("}", i + 3)
            if end != -1 and re.fullmatch(r"[0-9a-fA-F]+", inner[i + 3 : end]):
                out.append(chr(int(inner[i + 3 : end], 16)))
                i = end + 1
                changed = True
                continue
        if nxt.isdigit():
            j = i + 1
            while j < len(inner) and j < i + 4 and inner[j].isdigit():
                j += 1
            out.append(chr(int(inner[i + 1 : j], 8) & 0xFF))
            i = j
            changed = True
            continue
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
            changed = True
            continue
        out.append(char)
        i += 1
    if not changed:
        return match.group(0)
    return '"' + "".join(out) + '"'


def decode_escapes(text):
    """解码双引号字符串里的 \\x65、\\u{65}、八进制等转义。"""
    records = []

    def _collect(match):
        decoded = _decode_double_quoted(match)
        if decoded != match.group(0):
            records.append(
                {"technique": "escape_decode", "from": match.group(0), "to": decoded}
            )
        return decoded

    return _DQ_STRING.sub(_collect, text), records


def _decode_base64(arg):
    compact = arg.strip()
    if len(compact) < 8 or not _B64_TOKEN.match(compact):
        return None
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if _printable_ratio(raw) >= 0.85 else None


def _decode_hex(arg):
    if len(arg) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", arg):
        return None
    try:
        raw = bytes.fromhex(arg)
    except ValueError:
        return None
    return raw if _printable_ratio(raw) >= 0.85 else None


def _decode_inflate(arg):
    blob = arg.encode("latin-1", "ignore")
    for wbits in (-15, 15, 31):
        try:
            raw = zlib.decompress(blob, wbits)
        except zlib.error:
            continue
        if _printable_ratio(raw) >= 0.85:
            return raw
    return None


def _decode_wrapper(name, arg):
    if name == "base64_decode":
        return _decode_base64(arg)
    if name == "hex2bin":
        return _decode_hex(arg)
    if name in ("gzinflate", "gzuncompress"):
        return _decode_inflate(arg)
    if name == "str_rot13":
        import codecs

        return codecs.decode(arg, "rot_13").encode("utf-8", "replace")
    if name == "strrev":
        return arg[::-1].encode("utf-8", "replace")
    if name == "urldecode":
        from urllib.parse import unquote

        return unquote(arg).encode("utf-8", "replace")
    return None


def apply_wrappers(text):
    """对编码函数的字面量参数做一次解码，解不出可读内容就保留原样。"""
    records = []

    def _replace(match):
        name, quote, arg = match.group(1), match.group(2), match.group(3)
        raw = _decode_wrapper(name, arg)
        if raw is None:
            return match.group(0)
        decoded = raw.decode("utf-8", "replace")
        records.append(
            {
                "technique": name,
                "from": match.group(0)[:160],
                "to": decoded[:160],
            }
        )
        return quote + decoded.replace(quote, "\\" + quote) + quote

    return _WRAPPER.sub(_replace, text), records


_LITERAL_ASSIGN = re.compile(
    r"(\$[A-Za-z_]\w*)\s*=\s*(['\"])((?:\\.|(?!\2).)*)\2\s*;"
)
_WRAPPER_VAR = re.compile(
    r"\b(base64_decode|gzinflate|gzuncompress|str_rot13|strrev|urldecode|hex2bin)"
    r"\s*\(\s*(\$[A-Za-z_]\w*)\s*\)"
)


def resolve_wrapped_variables(text, variables=None):
    """处理把编码串先赋值给变量、再间接传入解码函数的写法。

    例如 $p = 'ZXZhbA=='; eval(base64_decode($p));
    会把 base64_decode($p) 原地替换成解码后的字面量，并回填变量映射供后续行使用。
    """
    variables = {} if variables is None else variables
    records = []

    for match in _LITERAL_ASSIGN.finditer(text):
        variables[match.group(1)] = (match.group(2), match.group(3))

    def _replace(match):
        name, variable = match.group(1), match.group(2)
        if variable not in variables:
            return match.group(0)
        quote, arg = variables[variable]
        raw = _decode_wrapper(name, arg)
        if raw is None:
            return match.group(0)
        decoded = raw.decode("utf-8", "replace")
        records.append(
            {
                "technique": name + "_via_var",
                "from": match.group(0)[:160],
                "to": decoded[:160],
            }
        )
        return quote + decoded.replace(quote, "\\" + quote) + quote

    return _WRAPPER_VAR.sub(_replace, text), records, variables


def restore_line(line, variables=None):
    """对单行做分层还原，返回还原后的文本和还原记录。

    variables 跨行保存字面量赋值，用来还原间接传入解码函数的编码串。
    """
    records = []
    text = line
    for _ in range(6):
        before = text
        for step in (fold_concatenation, replace_chr, decode_escapes, apply_wrappers):
            text, step_records = step(text)
            records.extend(step_records)
        text, step_records, variables = resolve_wrapped_variables(text, variables)
        records.extend(step_records)
        if text == before:
            break
    return text, records, variables


def restore_source(source):
    """逐行还原，保持行号与原文对齐。"""
    restored_lines, records, variables = [], [], {}
    for number, line in enumerate(source.splitlines(), start=1):
        restored, line_records, variables = restore_line(line, variables)
        restored_lines.append(restored)
        for record in line_records:
            record = dict(record)
            record["line"] = number
            records.append(record)
    return restored_lines, records
