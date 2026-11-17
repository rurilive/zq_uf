"""报告行级解析：兼容精简工具格式与 AddressSanitizer/LeakSanitizer 原始输出。

只负责把一段报告文本拆成结构化的 occurrence 字典（不带 id、不入注册表），
归并和注册表逻辑在 issues.py 中。
"""

import re

_HEX = re.compile(r"0x[0-9a-fA-F]+")
_ASAN_ERROR = re.compile(r"^==\d+==ERROR:\s*([A-Za-z]+Sanitizer):\s*(.*?)\s*$")
_SIMPLE_ERROR = re.compile(r"^ERROR:\s*([A-Za-z][A-Za-z-]*)\s*(.*?)\s*$")
_FRAME = re.compile(r"^\s*#(\d+)\s+(0x[0-9a-fA-F]+)\s*(?:in\s+)?(.*?)\s*$")
_ALLOC = re.compile(r"^ALLOC\s+(0x[0-9a-fA-F]+)\s*(.*?)\s*$")
_SECTION = re.compile(
    r"^(?:freed|previously allocated|allocated) by thread .* here:\s*$"
)
_LEAK = re.compile(r"^(Direct|Indirect) leak of .* allocated from:\s*$")
_SUMMARY = re.compile(r"^SUMMARY:\s*([A-Za-z]+Sanitizer):\s*(.*?)\s*$")
_SOURCE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?$")
_BINARY = re.compile(r"\([^()]*\)")


def is_error_marker(line):
    """一行是否是某个错误块的开头（两种格式都认）。"""
    s = line.strip()
    return s.startswith("ERROR:") or bool(_ASAN_ERROR.match(s))


def split_blocks(text):
    """按 ERROR 标记把整份报告切成块，块内保留原始行（含头部行）。"""
    blocks = []
    current = None
    for raw in text.splitlines():
        if is_error_marker(raw):
            if current is not None:
                blocks.append(current)
            current = [raw]
        elif current is not None:
            current.append(raw)
    if current is not None:
        blocks.append(current)
    return blocks


def _looks_like_file(token):
    """file:line 里的 file 需要像路径，避免把普通标识符误判成源码位置。"""
    return "." in token or "/" in token


def _parse_tail(tail):
    """拆帧尾部：'read_token parser.c:88:5' -> (function, source|None)。"""
    tail = _BINARY.sub("", tail).strip()  # 去掉 (/lib/libc.so.6+0x..) 这类纯二进制标记
    tokens = tail.split()
    source = None
    if tokens:
        m = _SOURCE.match(tokens[-1])
        if m and _looks_like_file(m.group("file")):
            tokens.pop()
            source = {
                "file": m.group("file"),
                "line": int(m.group("line")),
                "column": int(m.group("col")) if m.group("col") else None,
            }
    function = " ".join(tokens).strip() or None
    return function, source


def _build_frame(frame_match, raw):
    index, address, tail = frame_match.groups()
    function, source = _parse_tail(tail)
    return {
        "index": str(index),
        "address": address,
        "function": function,
        "source": source,
        # 映射不上时显式标 unmapped，绝不留空让调用方猜
        "mapping": "mapped" if source else "unmapped",
        "raw": raw,
    }


def _build_alloc(address, tail, raw, index=None):
    function, source = _parse_tail(tail)
    return {
        "index": index,
        "address": address,
        "function": function,
        "source": source,
        "mapping": "mapped" if source else "unmapped",
        "raw": raw,
    }


def _new_occ(occ_type, address, raw):
    return {
        "type": occ_type,
        "address": address,
        "frames": [],
        "freed_frames": [],
        "alloc": None,
        "alloc_frames": [],
        "leak_kind": None,
        "summary": None,
        "raw": raw,
    }


def _asan_type(remainder):
    """'heap-use-after-free on address ...' -> 'heap-use-after-free'。"""
    head = re.split(r"\s+on\s+|\s+at\s+|\s+which\s+", remainder, maxsplit=1)[0]
    return re.sub(r"[\s_]+", "-", head.strip().lower()).strip("-")


def _parse_summary(line):
    m = _SUMMARY.match(line)
    _, rest = m.groups()
    tokens = rest.split()
    function = None
    if len(tokens) >= 2 and tokens[-2] == "in":
        function = tokens[-1]
        tokens = tokens[:-2]
    source = None
    if tokens:
        sm = _SOURCE.match(tokens[-1])
        if sm and _looks_like_file(sm.group("file")):
            source = {
                "file": sm.group("file"),
                "line": int(sm.group("line")),
                "column": int(sm.group("col")) if sm.group("col") else None,
            }
            tokens = tokens[:-1]
    return {
        "raw": line,
        "type": tokens[0] if tokens else "",
        "function": function,
        "source": source,
    }


def _parse_lsan(body, raw):
    """LeakSanitizer 一个 ERROR 块里可能有 Direct/Indirect 多个泄漏段，每段一条。"""
    occs = []
    current = None
    for raw_line in body:
        s = raw_line.strip()
        lm = _LEAK.match(s)
        if lm:
            current = _new_occ("memory-leak", "", raw)
            current["leak_kind"] = lm.group(1).lower()
            occs.append(current)
            continue
        fm = _FRAME.match(s)
        if fm and current is not None:
            current["frames"].append(_build_frame(fm, s))
    return [o for o in occs if o["frames"]]


def parse_block(lines):
    """解析一个错误块。

    返回 (occurrences, reason)：
    - 成功时 occurrences 为 1..n 条结构化字典，reason 为 None；
    - 块像错误报告但结构解析不出来时返回 ([], reason)，由上层保留原文。
    """
    header = lines[0].strip()
    body = lines[1:]
    raw = "\n".join(lines)

    m = _ASAN_ERROR.match(header)
    if m:
        sanitizer, remainder = m.groups()
        if sanitizer == "LeakSanitizer":
            occs = _parse_lsan(body, raw)
            if not occs:
                return [], "LeakSanitizer block has no leak stack sections"
            return occs, None
        occ_type = _asan_type(remainder)
        addr = _HEX.search(remainder)
        address = addr.group(0) if addr else ""
    else:
        m = _SIMPLE_ERROR.match(header)
        if not m:
            return [], "unrecognized ERROR header: %s" % header
        occ_type = m.group(1)
        addr = _HEX.search(m.group(2))  # 地址可夹在描述文字中，如 SEGV on unknown address 0x..
        address = addr.group(0) if addr else ""

    occ = _new_occ(occ_type, address, raw)
    section = "stack"
    for raw_line in body:
        s = raw_line.strip()
        if not s:
            continue
        fm = _FRAME.match(s)
        if fm:
            frame = _build_frame(fm, s)
            if section == "stack":
                occ["frames"].append(frame)
            elif section == "freed":
                occ["freed_frames"].append(frame)
            else:
                occ["alloc_frames"].append(frame)
            continue
        am = _ALLOC.match(s)
        if am:  # 精简格式的 ALLOC 行
            alloc = _build_alloc(am.group(1), am.group(2), s)
            occ["alloc"] = alloc
            occ["alloc_frames"].append(dict(alloc))
            continue
        if _SECTION.match(s):
            section = "freed" if s.lower().startswith("freed") else "alloc"
            continue
        sm = _SUMMARY.match(s)
        if sm:
            occ["summary"] = _parse_summary(s)
            continue
        # 其余说明性文字（READ of size、==pid== 装饰行等）不影响结构提取

    if not occ["frames"] and not address:
        return [], "error block has neither address nor stack frames"
    if occ["alloc_frames"] and occ["alloc"] is None:
        occ["alloc"] = dict(occ["alloc_frames"][0])
    return [occ], None
