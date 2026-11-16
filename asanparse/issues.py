"""内存检测报告解析：按报告结构提取问题、调用栈和分配位置，按栈归并。

支持 AddressSanitizer 的常见输出：``ERROR: AddressSanitizer: <类型> ...``、
``READ/WRITE of size N at <地址>``、``#N <地址> in <函数> <文件>:<行>``、
``freed by thread ... here:``、``previously allocated by thread ... here:``
（或 ``allocated from:``）和 ``SUMMARY:``；同时兼容简化的
``ERROR: <类型> <地址>`` / ``#N <地址> <函数> <文件>:<行>`` /
``ALLOC <地址> <函数> <文件>:<行>``。

同一处问题在多次运行里地址会变，归并只看问题类型和调用栈的函数名（地址、
行号都不参与比较），所以多次 ``parse`` 会把同一处问题合并到一个条目，每次
报告的地址差异单独记在 ``address_diff``。帧上带 ``file:line`` 时视为能映射到
源码，映射结果放在 ``source``；也可以给 ``parse(..., symbols=...)`` 传地址到
源码的符号表。映射不上时条目和帧都显式标为 ``mapping="unmapped"``。

报告结构对不上时不静默返回空结果：``status`` 标为 ``failed``（没识别出任何
条目）或 ``partial``（部分行没认出来），原始文本保留在 ``raw``，认不出的行
逐条列在 ``rejected``。
"""

import copy
import fnmatch
import re

# ---------------------------------------------------------------------------
# 行格式
# ---------------------------------------------------------------------------

_ADDRESS = r"0x[0-9a-fA-F]+"

# ASan 报错头，可能带 ==pid== 前缀。
_HEADER_RE = re.compile(r"^\s*(?:==\d+==\s*)?ERROR:\s*(?P<rest>.*)$")

# 头里的检查器名称。
_SANITIZER_RE = re.compile(
    r"^(Address|Leak|Thread|Memory|UndefinedBehavior|HWAddress)Sanitizer:\s*"
)

# 错误类型只接受标识符形态，避免把普通日志里的 ERROR: 当成报告。
_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]*$")

# 头里的出错地址：on address / on unknown address / 直接 on 0x... / pc。
_HEADER_ADDRESS_RE = re.compile(
    r"(?:on\s+(?:unknown\s+)?address\s+|unknown\s+address\s+)"
    r"(" + _ADDRESS + r")"
)
# "attempting double-free on 0x602000000010 in thread T0" 这种没有 "address" 的写法。
_HEADER_ON_ADDRESS_RE = re.compile(r"\bon\s+(" + _ADDRESS + r")")
_PC_RE = re.compile(r"(?:\bat\s+pc\s+|\bpc\s+|\(pc\s+)(" + _ADDRESS + r")")

# 访问描述：READ/WRITE of size N at 0x... thread T0
_ACCESS_RE = re.compile(
    r"^(?P<op>READ|WRITE)\s+of\s+size\s+(?P<size>\d+)\s+at\s+"
    r"(?P<address>" + _ADDRESS + r")(?:\s+thread\s+(?P<thread>\S+))?"
)

# 栈帧： #0 0x... in func file:line:col
_FRAME_RE = re.compile(
    r"^\s*#(?P<index>\d+)\s+(?P<address>" + _ADDRESS + r")\b(?P<rest>.*)$"
)

# 简化格式的分配行：ALLOC 0x... func file:line
_ALLOC_LINE_RE = re.compile(
    r"^\s*ALLOC\s+(?P<address>" + _ADDRESS + r")\b(?P<rest>.*)$"
)

# ASan 段落标记：freed by thread T0 here: / allocated by thread T0 here:
# / allocated from:（泄漏报告里前面还带 “Direct leak of N byte(s)”）。
_FREE_SECTION_RE = re.compile(
    r"\b(?:freed|deallocated)\s+by\s+(?:thread\s+)?\S+.*here:\s*$",
    re.IGNORECASE,
)
_ALLOC_SECTION_RE = re.compile(
    r"\b(?:previously\s+)?allocated\s+(?:by\s+(?:thread\s+)?\S+.*here"
    r"|from):\s*$",
    re.IGNORECASE,
)

# 泄漏报告的每条泄漏：Direct leak of 32 byte(s) in 1 object(s) allocated from:
_LEAK_RE = re.compile(
    r"^\s*(?P<kind>Direct|Indirect)\s+leak\s+of\s+(?P<bytes>\d+)\s+byte",
    re.IGNORECASE,
)

# SUMMARY: AddressSanitizer: heap-use-after-free /src/parser.c:88:12 in f
_SUMMARY_RE = re.compile(r"^\s*SUMMARY:\s*(?P<rest>.+)$")

# 行尾的 文件:行[:列]，兼容 C:\path\foo.c 这种带盘符的写法。
_LOCATION_RE = re.compile(
    r"(?P<file>(?:[A-Za-z]:[\\/])?[^\s:()]+)"
    r":(?P<line>\d+)(?::(?P<column>\d+))?\s*$"
)

# 只有模块和偏移、没有源码位置：(libfoo.so+0x1234)
_MODULE_RE = re.compile(r"\((?P<module>[^()\s]+)\+0x[0-9a-fA-F]+\)\s*$")

_SEPARATOR_RE = re.compile(r"^\s*=+\s*$")

_DISCRIMINATOR_RE = re.compile(r"\s*\(discriminator \d+\)\s*$")

# 区域描述段：Address 0x... is located in stack of thread T0 ... / region 段
# 里的帧不再是崩溃栈，单独忽略，免得污染归并用的调用栈。
_REGION_RE = re.compile(
    r"^\s*(?:Address\s+" + _ADDRESS + r"\s+is\s+located"
    r"|This frame has\s|.*\bShadow bytes around the buggy address\b)",
    re.IGNORECASE,
)

# SEGV 报告用这句话说明访问类型，没有 READ/WRITE of size 行。
_SIGNAL_ACCESS_RE = re.compile(
    r"signal\s+is\s+caused\s+by\s+a\s+(?P<op>READ|WRITE)\s+memory\s+access",
    re.IGNORECASE,
)

# 多词类型收敛成 ASan 文档里的惯用名（原文首词 “attempting/detected” 没有意义）。
_KIND_PHRASES = (
    ("detected memory leaks", "memory-leak"),
    ("attempting free on address", "bad-free"),
    ("attempting double-free", "double-free"),
    ("calloc parameters overflow", "allocator-parameters-overflow"),
    ("malloc parameters overflow", "allocator-parameters-overflow"),
    ("out of memory", "out-of-memory"),
)

# ---------------------------------------------------------------------------
# 模块状态
#
# _ISSUES 保存归并后的条目，_GROUPS 把“类型 + 调用栈”映射到条目编号，
# 多次 parse 复用同一个条目。_REPORT_SEQ 给每次 parse 的报告编号。
# ---------------------------------------------------------------------------

_ISSUES = {}
_GROUPS = {}
_REPORT_SEQ = 0


def reset():
    """清空内存态，主要给测试和演示重复运行使用。"""
    global _REPORT_SEQ
    _ISSUES.clear()
    _GROUPS.clear()
    _REPORT_SEQ = 0


# ---------------------------------------------------------------------------
# 地址与符号
# ---------------------------------------------------------------------------

def _normalize_address(text):
    """把地址收敛成 ``0x`` + 小写十六进制，去掉前导零。"""
    if text is None:
        return ""
    if isinstance(text, int):
        return "0x%x" % text
    text = str(text).strip()
    if not text:
        return ""
    if text.lower().startswith("0x"):
        try:
            return "0x%x" % int(text, 16)
        except ValueError:
            return text.lower()
    return text.lower()


def _symbol_entry(value):
    """接受 ``{file, line, function}``、``(file, line, function)`` 或
    ``"file:line"`` / ``"func file:line"`` 形式的符号信息。"""
    if isinstance(value, str):
        location = _LOCATION_RE.search(value.strip())
        if not location:
            return None
        column = location.group("column")
        return {
            "file": location.group("file"),
            "line": int(location.group("line")),
            "column": int(column) if column else None,
            "function": value[: location.start()].strip(),
        }
    if isinstance(value, dict):
        line = value.get("line")
        if line is not None:
            try:
                line = int(line)
            except (TypeError, ValueError):
                line = None
        return {
            "file": value.get("file", "") or "",
            "line": line,
            "column": value.get("column"),
            "function": value.get("function", "") or "",
        }
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        line = value[1]
        try:
            line = int(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        return {
            "file": value[0] or "",
            "line": line,
            "column": None,
            "function": value[2] if len(value) > 2 else "",
        }
    return None


def _symbol_table(symbols):
    if symbols is None:
        return {}
    if not isinstance(symbols, dict):
        raise ValueError("symbols must be a dict")
    table = {}
    for key, value in symbols.items():
        entry = _symbol_entry(value)
        address = _normalize_address(key)
        if entry is not None and address:
            table[address] = entry
    return table


def _resolve_symbols(frame, table):
    """帧上已有源码位置就以帧为准，否则按地址查符号表。"""
    if frame["mapped"] or not table:
        return frame
    entry = table.get(_normalize_address(frame.get("address")))
    if not entry:
        return frame
    if not frame.get("function"):
        frame["function"] = entry.get("function", "")
    if not frame.get("file"):
        frame["file"] = entry.get("file", "")
    if frame.get("line") is None:
        frame["line"] = entry.get("line")
    if frame.get("column") is None:
        frame["column"] = entry.get("column")
    frame["mapped"] = bool(frame.get("file")) and frame.get("line") is not None
    return frame


# ---------------------------------------------------------------------------
# 单行解析
# ---------------------------------------------------------------------------

def _split_frame_rest(rest):
    """把 ``#0 0x...`` 之后的部分拆成函数、文件、行号和模块。

    提取不到的字段留空，``mapped`` 只在拿到文件与行号时为 True。
    """
    rest = _DISCRIMINATOR_RE.sub("", rest or "").strip()
    inline = ""
    if "(inlined by)" in rest:
        primary, _, inline = rest.partition("(inlined by)")
        rest = primary.strip()
        inline = inline.strip()
    module = ""
    module_match = _MODULE_RE.search(rest)
    if module_match:
        module = module_match.group("module")
        rest = rest[: module_match.start()].strip()
    file = ""
    line = None
    column = None
    location = _LOCATION_RE.search(rest)
    if location:
        file = location.group("file")
        line = int(location.group("line"))
        if location.group("column"):
            column = int(location.group("column"))
        rest = rest[: location.start()].strip()
    if rest.startswith("in "):
        rest = rest[3:].strip()
    return {
        "function": rest.strip(),
        "file": file,
        "line": line,
        "column": column,
        "module": module,
        "inline": inline,
        "mapped": bool(file) and line is not None,
    }


def _kind_phrase(rest):
    """从头部剩余文本取错误类型。"""
    lowered = rest.lower()
    for phrase, name in _KIND_PHRASES:
        if lowered.startswith(phrase):
            return name, rest[len(phrase):].strip()
    for separator in (
        " on unknown address",
        " on address",
        " on ",
        " (pc",
        " at pc",
        " at ",
        " in ",
        ":",
    ):
        position = rest.find(separator)
        if position > 0:
            rest = rest[:position]
            break
    return rest.strip().split(" ", 1)[0] if rest.strip() else "", ""


def _parse_header(line):
    """解析 ``ERROR:`` 行；结构不对返回 None。"""
    match = _HEADER_RE.match(line)
    if not match:
        return None
    rest = match.group("rest").strip()
    if not rest:
        return None
    sanitizer = ""
    sanitizer_match = _SANITIZER_RE.match(rest)
    if sanitizer_match:
        sanitizer = sanitizer_match.group(1) + "Sanitizer"
        rest = rest[sanitizer_match.end():].strip()
        kind, _ = _kind_phrase(rest)
        address_match = _HEADER_ADDRESS_RE.search(rest)
        if not address_match:
            address_match = _HEADER_ON_ADDRESS_RE.search(rest)
        if not address_match:
            address_match = _PC_RE.search(rest)
        address = address_match.group(1) if address_match else ""
    else:
        tokens = rest.split()
        kind = tokens[0].rstrip(":")
        address = ""
        for token in tokens[1:]:
            if re.fullmatch(_ADDRESS, token):
                address = token
                break
    if kind.upper().startswith("SEGV"):
        kind = "SEGV"
    if not _KIND_RE.match(kind):
        return None
    return {"type": kind, "address": address, "sanitizer": sanitizer}


def _parse_access(line):
    match = _ACCESS_RE.match(line)
    if not match:
        return None
    return {
        "op": match.group("op"),
        "size": int(match.group("size")),
        "address": match.group("address"),
        "thread": match.group("thread") or "",
    }


def _parse_signal_access(line):
    """SEGV 报告里的 “caused by a READ memory access”，没有 size。"""
    match = _SIGNAL_ACCESS_RE.search(line)
    if not match:
        return None
    return {"op": match.group("op"), "size": None, "address": "", "thread": ""}


def _parse_summary(line):
    """解析 SUMMARY 行，作为调用栈缺失时的兜底类型和源码位置。"""
    match = _SUMMARY_RE.match(line)
    if not match:
        return None
    rest = match.group("rest").strip()
    if not rest:
        return None
    sanitizer_match = _SANITIZER_RE.match(rest)
    if sanitizer_match:
        rest = rest[sanitizer_match.end():].strip()
    kind, _ = _kind_phrase(rest)
    if kind.upper().startswith("SEGV"):
        kind = "SEGV"
    file = ""
    line_number = None
    column = None
    for token in rest.split():
        location = re.fullmatch(
            r"(?P<file>[^\s:()]+):(?P<line>\d+)(?::(?P<column>\d+))?", token
        )
        if location:
            file = location.group("file")
            line_number = int(location.group("line"))
            if location.group("column"):
                column = int(location.group("column"))
            break
    function = rest.rsplit(" in ", 1)[1].strip() if " in " in rest else ""
    return {
        "type": kind,
        "function": function,
        "file": file,
        "line": line_number,
        "column": column,
        "mapped": bool(file) and line_number is not None,
    }


def _source(frame):
    """取一帧的源码位置，映射不上时 mapped=False。"""
    return {
        "file": frame.get("file", ""),
        "line": frame.get("line"),
        "column": frame.get("column"),
        "function": frame.get("function", ""),
        "module": frame.get("module", ""),
        "mapped": bool(frame.get("mapped")),
    }


def _summary_source(summary):
    if not summary:
        return {
            "file": "",
            "line": None,
            "column": None,
            "function": "",
            "module": "",
            "mapped": False,
        }
    return {
        "file": summary["file"],
        "line": summary["line"],
        "column": summary["column"],
        "function": summary["function"],
        "module": "",
        "mapped": summary["mapped"],
    }


def _primary_frame(frames):
    """取能定位到源码的那一帧；都映射不上时退回第一帧，保持原样标出。"""
    for frame in frames:
        if frame.get("mapped"):
            return frame
    return frames[0] if frames else None


# ---------------------------------------------------------------------------
# 分块
# ---------------------------------------------------------------------------

def _split_blocks(text):
    """按 ``ERROR:`` 行切段，逐段收集调用栈、分配/释放栈和访问信息。

    LeakSanitizer 的泄漏报告一个 ``ERROR:`` 下有多条 ``Direct/Indirect leak``，
    每条泄漏单独成段，脚本里各自归并，不把不同泄漏位置的栈混在一起。
    """
    blocks = []
    rejected = []
    current = None
    section = None
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        header_match = _HEADER_RE.match(line)
        if header_match:
            header = _parse_header(line)
            if header is None:
                rejected.append({
                    "line": number,
                    "text": raw_line,
                    "reason": "unrecognized error header",
                })
                current = None
                section = None
                continue
            current = {
                "header": header,
                "frames": [],
                "free": [],
                "alloc": [],
                "access": None,
                "summary": None,
                "raw": [raw_line],
                "start": number,
            }
            blocks.append(current)
            section = "crash"
            continue
        if current is None:
            # 块外的日志只当上下文，不当成格式错误。
            continue
        current["raw"].append(raw_line)
        if _SEPARATOR_RE.match(raw_line):
            continue
        if _REGION_RE.match(line):
            # 区域描述段只说明越界落在哪个对象上，里面的帧不是崩溃栈。
            section = "region"
            continue
        summary = _parse_summary(line)
        if summary is not None:
            current["summary"] = summary
            continue
        if section == "region":
            continue
        leak_match = _LEAK_RE.match(line)
        if leak_match and current["header"]["type"] == "memory-leak":
            # 同一个 ERROR 下的下一条泄漏：克隆头部，单独成段。
            current = {
                "header": dict(current["header"]),
                "frames": [],
                "free": [],
                "alloc": [],
                "access": None,
                "summary": None,
                "raw": [raw_line],
                "start": number,
                "leak": leak_match.group("kind").lower(),
                "bytes": int(leak_match.group("bytes")),
            }
            blocks.append(current)
            section = "alloc"
            continue
        if _FREE_SECTION_RE.search(line):
            section = "free"
            continue
        if _ALLOC_SECTION_RE.search(line):
            section = "alloc"
            continue
        alloc_match = _ALLOC_LINE_RE.match(line)
        if alloc_match:
            body = _split_frame_rest(alloc_match.group("rest"))
            body["index"] = None
            body["address"] = alloc_match.group("address")
            body["raw"] = raw_line
            current["alloc"].append(body)
            continue
        if line.startswith("#"):
            frame_match = _FRAME_RE.match(line)
            if frame_match is None:
                rejected.append({
                    "line": number,
                    "text": raw_line,
                    "reason": "unrecognized stack frame",
                })
                continue
            body = _split_frame_rest(frame_match.group("rest"))
            body["index"] = int(frame_match.group("index"))
            body["address"] = frame_match.group("address")
            body["raw"] = raw_line
            target = (
                current["frames"] if section == "crash" else current[section]
            )
            target.append(body)
            continue
        access = _parse_access(line)
        if access is not None:
            current["access"] = access
            continue
        signal_access = _parse_signal_access(line)
        if signal_access is not None:
            current["access"] = signal_access
            continue
        summary = _parse_summary(line)
        if summary is not None:
            current["summary"] = summary
            continue
        # 其余是 region 描述、ABORTING 之类的说明行，忽略。
    return blocks, rejected


# ---------------------------------------------------------------------------
# 归并
# ---------------------------------------------------------------------------

def _stack_identity(frame):
    """一帧的稳定标识：函数名优先，没有函数名时才退回文件名/模块/原始行。

    地址和行号在多次运行、不同构建里都可能变，不能用来判定是不是同一处。
    函数名是栈里最稳定的部分；有些运行没带 ``file:line``，若把文件名算进
    标识，同一次崩溃就会因为“这轮有没有符号”被拆成两个条目，所以文件名
    只在没有函数名时兜底。
    """
    function = (frame.get("function") or "").strip()
    if function:
        return function
    file = (frame.get("file") or "").strip()
    basename = file.replace("\\", "/").rsplit("/", 1)[-1]
    if basename:
        return "@%s" % basename
    module = (frame.get("module") or "").strip()
    if module:
        return module
    return _strip_addresses(frame.get("raw", "")) or "?"


def _strip_addresses(text):
    return re.sub(_ADDRESS, "0x?", text or "")


def _collapse(frames):
    """折叠连续重复的帧，栈溢出在递归深处会重复同一个函数很多层。"""
    collapsed = []
    for frame in frames:
        identity = _stack_identity(frame)
        if collapsed and collapsed[-1] == identity:
            continue
        collapsed.append(identity)
    return tuple(collapsed)


def _group_key(occurrence):
    """类型 + 调用栈的函数名。地址和行号每次都可能变，不参与归并。"""
    frames = occurrence["frames"]
    if frames:
        stack = _collapse(frames)
    elif occurrence.get("leak") and occurrence.get("alloc_stack"):
        # 泄漏没有崩溃栈，用整条分配栈区分不同的泄漏点。
        stack = _collapse(occurrence["alloc_stack"])
    elif occurrence["source"]["mapped"]:
        stack = (_stack_identity(occurrence["source"]),)
    elif (
        occurrence["alloc"].get("mapped")
        or occurrence["alloc"].get("function")
    ):
        stack = (_stack_identity(occurrence["alloc"]),)
    else:
        stack = (_strip_addresses(occurrence["raw"]),)
    return occurrence["type"], stack


def _key_label(key):
    kind, stack = key
    return kind + " :: " + " <- ".join(stack)


def _occurrence_from_block(block, report_index, symbols):
    """把一段报告变成一次出现记录；没有栈也没有分配位置时返回 None。"""
    header = block["header"]
    frames = block["frames"]
    alloc_frames = block["alloc"]
    free_frames = block["free"]
    summary = block["summary"]
    if not frames and not alloc_frames and summary is None:
        return None
    for frame in frames + alloc_frames + free_frames:
        _resolve_symbols(frame, symbols)
    access = block["access"] or {}
    # 出错地址优先取头部 “on address”；没有时用访问行的地址（真正越界/读写的
    # 地址）；最后才退回栈顶帧里的 pc。帧地址是出错指令的地址，不是被访问的
    # 地址，直接用会把两种地址混在一起。
    address = header.get("address") or ""
    if not address:
        address = access.get("address", "")
    if not address and frames:
        address = frames[0]["address"]
    if frames:
        source = _source(_primary_frame(frames))
    elif alloc_frames and block.get("leak"):
        # 泄漏报告没有崩溃栈，出问题的就是分配位置。
        source = _source(_primary_frame(alloc_frames))
    else:
        source = _summary_source(summary)
    alloc = {}
    if alloc_frames:
        primary = _primary_frame(alloc_frames)
        alloc = _source(primary)
        alloc["address"] = primary["address"]
    free = {}
    if free_frames:
        primary = _primary_frame(free_frames)
        free = _source(primary)
        free["address"] = primary["address"]
    occurrence = {
        "report": report_index,
        "type": header["type"],
        "address": address,
        "access": dict(access),
        "frames": frames,
        "source": source,
        "alloc": alloc,
        "alloc_stack": list(alloc_frames),
        "free": free,
        "raw": "\n".join(block["raw"]),
    }
    if block.get("leak"):
        occurrence["leak"] = {
            "kind": block["leak"],
            "bytes": block.get("bytes", 0),
        }
    return occurrence


def _unique_sources(occurrences):
    sources = []
    seen = set()
    for item in occurrences:
        source = item["source"]
        if not source.get("mapped"):
            continue
        key = (source["file"], source["line"], source["function"])
        if key in seen:
            continue
        seen.add(key)
        sources.append(dict(source))
    return sources


def _refresh(issue):
    """重算归并条目的地址差异、映射状态和计数。"""
    occurrences = issue["occurrences"]
    issue["count"] = len(occurrences)
    issue["reports"] = [item["report"] for item in occurrences]

    # 地址按数值去重，0x000000401234 和 0x401234 视为同一个。
    seen = {}
    for item in occurrences:
        if not item["address"]:
            continue
        seen.setdefault(_normalize_address(item["address"]), item["address"])
    addresses = list(seen.values())
    issue["addresses"] = addresses
    issue["address_changed"] = len(addresses) > 1

    diff = []
    if len(addresses) > 1:
        for normalized, address in seen.items():
            related = [
                item
                for item in occurrences
                if _normalize_address(item["address"]) == normalized
            ]
            source = next(
                (
                    item["source"]
                    for item in related
                    if item["source"]["mapped"]
                ),
                related[0]["source"],
            )
            diff.append({
                "address": address,
                "count": len(related),
                "reports": [item["report"] for item in related],
                "file": source["file"],
                "line": source["line"],
                "column": source["column"],
                "function": source["function"],
                "mapped": source["mapped"],
            })
    issue["address_diff"] = diff
    issue["sources"] = _unique_sources(occurrences)

    leaks = [item["leak"] for item in occurrences if item.get("leak")]
    if leaks:
        issue["leak"] = {
            "kind": next(
                (item["kind"] for item in leaks if item["kind"] == "direct"),
                leaks[0]["kind"],
            ),
            "bytes": max(item["bytes"] for item in leaks),
            "total_bytes": sum(item["bytes"] for item in leaks),
        }
    else:
        issue.pop("leak", None)

    source = issue["source"]
    issue["mapping"] = "mapped" if source.get("mapped") else "unmapped"


def _new_id():
    number = len(_ISSUES) + 1
    while "issue-%d" % number in _ISSUES:
        number += 1
    return "issue-%d" % number


def _merge(occurrence):
    """把一次出现合并进已归并的条目，返回条目。"""
    key = _group_key(occurrence)
    issue_id = _GROUPS.get(key)
    if issue_id is not None and issue_id not in _ISSUES:
        # 外部清空过条目表，旧的分组映射不能再用。
        issue_id = None
    if issue_id is None:
        issue_id = _new_id()
        _GROUPS[key] = issue_id
        issue = {
            "id": issue_id,
            "type": occurrence["type"],
            "group": _key_label(key),
            "address": occurrence["address"],
            "addresses": [],
            "address_diff": [],
            "address_changed": False,
            "frames": occurrence["frames"],
            "source": occurrence["source"],
            "sources": [],
            "mapping": (
                "mapped" if occurrence["source"]["mapped"] else "unmapped"
            ),
            "access": {},
            "alloc": occurrence["alloc"],
            "free": occurrence["free"],
            "occurrences": [],
            "count": 0,
            "reports": [],
        }
        if occurrence.get("leak"):
            issue["leak"] = dict(occurrence["leak"])
        _ISSUES[issue_id] = issue
    issue = _ISSUES[issue_id]
    issue["occurrences"].append(occurrence)
    if not issue["source"]["mapped"] and occurrence["source"]["mapped"]:
        issue["source"] = occurrence["source"]
        issue["frames"] = occurrence["frames"]
    if not issue["frames"] and occurrence["frames"]:
        issue["frames"] = occurrence["frames"]
    if not issue["access"] and occurrence["access"]:
        issue["access"] = dict(occurrence["access"])
    if not issue["alloc"] and occurrence["alloc"]:
        issue["alloc"] = occurrence["alloc"]
    if not issue["free"] and occurrence["free"]:
        issue["free"] = occurrence["free"]
    _refresh(issue)
    return issue


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def _validate_filter(wanted):
    if wanted is None:
        return
    if isinstance(wanted, (list, tuple, set, frozenset)):
        for item in wanted:
            _validate_filter(item)
        return
    if not isinstance(wanted, str):
        raise ValueError(
            "error_type must be a string or a collection of strings"
        )


def _matches(error_type, wanted):
    if wanted is None:
        return True
    if isinstance(wanted, (list, tuple, set, frozenset)):
        return any(_matches(error_type, item) for item in wanted)
    if not isinstance(wanted, str):
        raise ValueError(
            "error_type must be a string or a collection of strings"
        )
    if any(char in wanted for char in "*?["):
        return fnmatch.fnmatchcase(error_type, wanted)
    return error_type == wanted


def _summary_of(issue):
    summary = {
        "id": issue["id"],
        "type": issue["type"],
        "address": issue["address"],
        "addresses": list(issue["addresses"]),
        "address_changed": issue["address_changed"],
        "address_diff": copy.deepcopy(issue["address_diff"]),
        "source": dict(issue["source"]),
        "sources": copy.deepcopy(issue["sources"]),
        "mapping": issue["mapping"],
        "access": dict(issue["access"]),
        "alloc": dict(issue["alloc"]),
        "occurrences": issue["count"],
        "reports": list(issue["reports"]),
        "group": issue["group"],
    }
    if "leak" in issue:
        summary["leak"] = dict(issue["leak"])
    return summary


def parse(text, error_type=None, symbols=None):
    """解析一份报告，返回按调用栈归并后的条目。

    ``error_type`` 可选，按问题类型过滤返回的条目（支持 ``heap-*`` 通配）。
    ``symbols`` 可选，地址到源码的符号表，帧上没有 ``file:line`` 时按地址查。

    归并发生在模块内：多次 ``parse`` 的同一处问题会合并到一个条目，
    ``occurrences`` 里保留每次报告的出现记录和地址。

    报告结构对不上时不返回空的假结果：``status`` 为 ``failed`` 或
    ``partial``，``raw`` 保留原文，``rejected`` 列出认不出的行。
    """
    if not isinstance(text, str):
        raise ValueError("report must be a string")
    _validate_filter(error_type)

    if not text.strip():
        return {
            "issues": [],
            "count": 0,
            "kept": 0,
            "occurrences": 0,
            "mapped": 0,
            "unmapped": 0,
            "types": {},
            "status": "empty",
            "rejected": [],
            "raw": "",
            "report": 0,
        }

    table = _symbol_table(symbols)

    global _REPORT_SEQ
    _REPORT_SEQ += 1
    report_index = _REPORT_SEQ

    blocks, rejected = _split_blocks(text)
    touched = []
    kept = 0
    for block in blocks:
        occurrence = _occurrence_from_block(block, report_index, table)
        if occurrence is None:
            # 泄漏报告的 ERROR 头只是容器，内容在各条 leak 段里。
            if block["header"]["type"] == "memory-leak":
                continue
            rejected.append({
                "line": block["start"],
                "text": "\n".join(block["raw"]),
                "reason": "block has no stack or allocation location",
            })
            continue
        kept += 1
        issue = _merge(occurrence)
        if all(issue["id"] != item["id"] for item in touched):
            touched.append(issue)

    if not touched:
        reason = (
            "error headers present but unrecognized"
            if rejected
            else "no report structure found"
        )
        if not rejected:
            rejected = [{
                "line": 0,
                "text": text,
                "reason": "no report structure found",
            }]
        return {
            "issues": [],
            "count": 0,
            "kept": 0,
            "occurrences": 0,
            "mapped": 0,
            "unmapped": 0,
            "types": {},
            "status": "failed",
            "reason": reason,
            "rejected": rejected,
            "raw": text,
            "report": report_index,
        }

    status = "partial" if rejected else "ok"
    selected = [
        copy.deepcopy(issue)
        for issue in touched
        if _matches(issue["type"], error_type)
    ]
    types = {}
    for issue in selected:
        types[issue["type"]] = types.get(issue["type"], 0) + 1
    return {
        "issues": selected,
        "count": len(selected),
        "kept": kept,
        "occurrences": kept,
        "mapped": sum(1 for issue in selected if issue["mapping"] == "mapped"),
        "unmapped": sum(
            1 for issue in selected if issue["mapping"] == "unmapped"
        ),
        "types": types,
        "status": status,
        "rejected": rejected,
        "raw": text if status == "partial" else "",
        "report": report_index,
    }


def locate(issue_id):
    """按条目编号取一条归并结果；取不到返回空字典。"""
    issue = _ISSUES.get(issue_id)
    if issue is None:
        return {}
    return copy.deepcopy(issue)


def report(error_type=None):
    """汇总当前归并结果，可按问题类型过滤。"""
    _validate_filter(error_type)
    issues = [
        issue
        for issue in _ISSUES.values()
        if _matches(issue["type"], error_type)
    ]
    types = {}
    for issue in issues:
        types[issue["type"]] = types.get(issue["type"], 0) + 1
    groups = [_summary_of(issue) for issue in issues]
    return {
        "issues": len(issues),
        "occurrences": sum(issue["count"] for issue in issues),
        "types": types,
        "groups": groups,
        "mapped": sum(1 for issue in issues if issue["mapping"] == "mapped"),
        "unmapped": sum(
            1 for issue in issues if issue["mapping"] == "unmapped"
        ),
        "address_changed": sum(
            1 for issue in issues if issue["address_changed"]
        ),
        "reports": _REPORT_SEQ,
    }
