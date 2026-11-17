"""多种沙箱报告格式的识别与解析。

支持三种格式，同批样本格式不一致时各自走对应解析器：

1. flat      —— 顶层带 ``behaviors`` 列表的 dict（原始格式）
2. sectioned —— 顶层带 ``sections``，行为按网络/文件/进程/注册表分节
3. text      —— 纯文本逐行日志，行首 ``[秒数] 类型: 动作``

认不出来的格式抛 :class:`UnknownReportFormatError`，原文随异常保留。
"""

from .errors import UnknownReportFormatError
from .rules import TYPES, normalize_action, section_type

_EVENT_ALIASES = {
    "action": ("action", "detail", "op", "operation", "command"),
    "seconds": ("seconds", "time", "timestamp", "at"),
}


def _pick(mapping, keys):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _event(behavior_type, action, seconds):
    if action is None or str(action).strip() == "":
        raise ValueError("behavior event missing action")
    if behavior_type not in TYPES:
        raise ValueError("unknown behavior type: {!r}".format(behavior_type))
    return {
        "type": behavior_type,
        "action": str(action).strip(),
        "seconds": seconds,
        "norm": normalize_action(action),
    }


def _parse_flat(report):
    events = []
    for entry in report.get("behaviors", []):
        events.append(_event(
            entry.get("type"),
            _pick(entry, _EVENT_ALIASES["action"]),
            entry.get("seconds"),
        ))
    return events, dict(report.get("static") or {})


def _parse_sectioned(report):
    events = []
    for section in report.get("sections", []):
        behavior_type = section_type(section.get("title"))
        if behavior_type is None:
            # 不认识的节保留不到行为里，但不让它静默污染四类行为
            continue
        for entry in section.get("events", []):
            events.append(_event(
                behavior_type,
                _pick(entry, _EVENT_ALIASES["action"]),
                _pick(entry, _EVENT_ALIASES["seconds"]),
            ))
    return events, dict(report.get("static") or {})


def _coerce_static_value(raw):
    value = raw.strip()
    lowered = value.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_text(report):
    events = []
    static = {}
    for lineno, raw_line in enumerate(str(report).splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("static:"):
            payload = line.split(":", 1)[1]
            if "=" not in payload:
                raise UnknownReportFormatError(
                    "line {}: static line needs key=value".format(lineno),
                    raw=report,
                )
            key, value = payload.split("=", 1)
            static[key.strip()] = _coerce_static_value(value)
            continue
        head, sep, tail = line.partition(":")
        if not sep:
            raise UnknownReportFormatError(
                "line {}: expected '[seconds] type: action'".format(lineno),
                raw=report,
            )
        head_parts = head.strip().split(None, 1)
        # 正常结构：[12] network: connect 10.0.0.5:443
        if len(head_parts) != 2:
            raise UnknownReportFormatError(
                "line {}: expected '[seconds] type: action'".format(lineno),
                raw=report,
            )
        seconds_text, behavior_type = head_parts
        if not (seconds_text.startswith("[") and seconds_text.endswith("]")):
            raise UnknownReportFormatError(
                "line {}: timestamp must be [seconds]".format(lineno),
                raw=report,
            )
        try:
            seconds = int(seconds_text[1:-1])
        except ValueError:
            raise UnknownReportFormatError(
                "line {}: timestamp must be integer seconds".format(lineno),
                raw=report,
            )
        events.append(_event(behavior_type.strip().lower(), tail, seconds))
    if not events and not static:
        raise UnknownReportFormatError(
            "text report contains no events or static lines", raw=report,
        )
    return events, static


def detect_format(report):
    """返回报告格式名，认不出来抛 UnknownReportFormatError。"""
    if isinstance(report, str):
        return "text"
    if isinstance(report, dict):
        if "id" not in report:
            raise UnknownReportFormatError(
                "report missing sample id", raw=report,
            )
        if "behaviors" in report:
            return "flat"
        if "sections" in report:
            return "sectioned"
        raise UnknownReportFormatError(
            "report has neither 'behaviors' nor 'sections'", raw=report,
        )
    raise UnknownReportFormatError(
        "report must be a dict or text log, got {}".format(
            type(report).__name__),
        raw=report,
    )


def extract_id(report):
    """从原始报告里取样本 id，文本格式取 ``# id: xxx`` 头。"""
    if isinstance(report, dict):
        return report.get("id")
    for raw_line in str(report).splitlines():
        line = raw_line.strip()
        if line.lower().startswith("# id:"):
            return line.split(":", 1)[1].strip()
    return None


def parse_report(report):
    """识别并解析任意受支持的报告，返回 (sample_id, 事件列表, 静态特征, 格式名)。"""
    fmt = detect_format(report)
    if fmt == "flat":
        events, static = _parse_flat(report)
    elif fmt == "sectioned":
        events, static = _parse_sectioned(report)
    else:
        events, static = _parse_text(report)
    sample_id = extract_id(report)
    if not sample_id:
        raise UnknownReportFormatError("report missing sample id", raw=report)
    return sample_id, events, static, fmt
