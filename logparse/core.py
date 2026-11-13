"""解析流程：格式识别、堆栈归并、失败保留、统计。"""

import re
from collections import namedtuple

from .formats import FORMATS, ParseError

ParseResult = namedtuple("ParseResult", ["records", "failures", "stats"])

_STACK_HINT = re.compile(
    r"^(Traceback|File |at |Caused by:|[\w.]+(Error|Exception)\b)"
)


def _is_continuation(line):
    return line[:1].isspace() or bool(_STACK_HINT.match(line))


def parse_result(lines):
    """解析行序列，返回 ParseResult(records, failures, stats)。"""
    records = []
    failures = []
    format_stats = {}
    total = 0
    merged = 0
    for line in lines:
        if not line.strip():
            continue
        total += 1
        fmt = next((f for f in FORMATS if f.match(line)), None)
        if fmt is None:
            if _is_continuation(line) and records:
                previous = records[-1]
                previous["text"] = (
                    previous["text"] + "\n" + line if previous["text"] else line
                )
                merged += 1
                continue
            reason = (
                "continuation without preceding record"
                if _is_continuation(line)
                else "no format matched"
            )
            failures.append({"raw": line, "reason": reason})
            continue
        try:
            record = fmt.parse(line)
        except ParseError as exc:
            failures.append({"raw": line, "reason": "%s: %s" % (fmt.name, exc)})
            continue
        records.append(record)
        format_stats[fmt.name] = format_stats.get(fmt.name, 0) + 1
    stats = {
        "total": total,
        "parsed": len(records),
        "failed": len(failures),
        "merged": merged,
        "formats": format_stats,
    }
    return ParseResult(records, failures, stats)


def parse(lines):
    """解析行序列，只返回成功记录的列表。"""
    return parse_result(lines).records
