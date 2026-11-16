"""报告格式适配层。

支持三种来源的沙箱报告，统一输出为内部标准结构::

    {
        "id": str,
        "format": "dict-behaviors" | "dict-sections" | "text",
        "entries": [{"type", "action", "seconds", "suspicious"}],
        "static": {...},
    }

无法识别的格式抛 :class:`ReportParseError`，并在异常对象上保留原文。
"""

import re

from .errors import ReportParseError
from .rules import CANONICAL_TYPES

# 文本报告分节标题 -> 标准结构字段
_TEXT_SECTIONS = {
    "网络行为": "network",
    "网络": "network",
    "文件行为": "file",
    "文件": "file",
    "进程行为": "process",
    "进程": "process",
    "注册表行为": "registry",
    "注册表": "registry",
    "静态特征": "static",
    "静态": "static",
}

_TIMESTAMP_RE = re.compile(r"^\[(\d{1,2}):(\d{2})\]\s*(.+?)\s*$")
_SECTION_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*$")
_ID_RE = re.compile(r"^\s*样本\s*ID\s*[:：]\s*(\S+)\s*$")
_STATIC_KV_RE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*[:=：]\s*(.+?)\s*$")
_LIST_SPLIT_RE = re.compile(r"[,，;；]\s*")
_SUSPICIOUS_TAG_RE = re.compile(r"（?\[(?:异常|可疑)\]\)?|\[(?:异常|可疑)\]", re.IGNORECASE)


def _coerce_seconds(value):
    """把时间字段归一化为秒数；无法解析时返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        iso = re.match(r"^\d{2}:\d{2}:\d{2}$", text)
        if iso:
            hours, minutes, seconds = (int(part) for part in text.split(":"))
            return hours * 3600 + minutes * 60 + seconds
        clock = re.match(r"^(\d{1,2}):(\d{2})$", text)
        if clock:
            return int(clock.group(1)) * 60 + int(clock.group(2))
    return None


def _coerce_static_value(raw):
    """静态特征值：列表样式的字符串拆成列表，其余转布尔/数字/字符串。"""
    text = raw.strip()
    if re.search(r"[,，;；]", text):
        return [part.strip() for part in _LIST_SPLIT_RE.split(text) if part.strip()]
    low = text.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _normalize_entry(raw, index):
    """把单条行为记录归一化，字段缺失或类型未知直接报错。"""
    if not isinstance(raw, dict):
        raise ReportParseError(f"behavior entry #{index} 不是对象: {raw!r}", raw)
    if "action" not in raw:
        raise ReportParseError(
            f"behavior entry #{index} 缺少字段 'action'", raw
        )
    action = raw["action"]
    if not isinstance(action, str) or not action.strip():
        raise ReportParseError(
            f"behavior entry #{index} 的 action 为空或不是字符串", raw
        )
    # type 允许缺失：分节格式下由所属分节补齐。
    behavior_type = raw.get("type")
    if behavior_type not in CANONICAL_TYPES:
        # 未知类型保留原文条目，交给上层做异常标记。
        pass
    seconds = _coerce_seconds(raw.get("seconds", raw.get("time", raw.get("timestamp"))))
    suspicious = bool(raw.get("suspicious", raw.get("anomaly", False)))
    reason = raw.get("reason")
    entry = {
        "type": behavior_type,
        "action": action.strip(),
        "seconds": seconds,
        "suspicious": suspicious,
    }
    if reason:
        entry["reason"] = str(reason)
    return entry


def _parse_text(report):
    """解析形如 ``[网络行为]`` + ``[mm:ss] 动作`` 的文本报告。"""
    sample_id = None
    section = None
    entries = []
    static = {}

    for lineno, raw_line in enumerate(report.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        id_match = _ID_RE.match(line)
        if id_match:
            sample_id = id_match.group(1)
            continue

        section_match = _SECTION_RE.match(line)
        if section_match:
            title = section_match.group(1).strip()
            if title not in _TEXT_SECTIONS:
                raise ReportParseError(
                    f"第 {lineno} 行分节无法识别: {title!r}", report
                )
            section = _TEXT_SECTIONS[title]
            continue

        if section is None:
            raise ReportParseError(
                f"第 {lineno} 行出现在任何分节之前: {line!r}", report
            )

        if section == "static":
            kv = _STATIC_KV_RE.match(line)
            if not kv:
                raise ReportParseError(
                    f"第 {lineno} 行静态特征格式应为 key=value: {line!r}", report
                )
            static[kv.group(1)] = _coerce_static_value(kv.group(2))
            continue

        ts_match = _TIMESTAMP_RE.match(line)
        if not ts_match:
            raise ReportParseError(
                f"第 {lineno} 行缺少 [mm:ss] 时间戳: {line!r}", report
            )
        seconds = int(ts_match.group(1)) * 60 + int(ts_match.group(2))
        body = ts_match.group(3)
        suspicious = bool(_SUSPICIOUS_TAG_RE.search(body))
        action = _SUSPICIOUS_TAG_RE.sub("", body).strip()
        if not action:
            raise ReportParseError(f"第 {lineno} 行动作为空", report)
        entries.append(
            {
                "type": section,
                "action": action,
                "seconds": seconds,
                "suspicious": suspicious,
            }
        )

    if not sample_id:
        raise ReportParseError("文本报告缺少“样本 ID”行", report)
    if section is None:
        raise ReportParseError("文本报告没有任何分节", report)
    return {
        "id": sample_id,
        "format": "text",
        "entries": entries,
        "static": static,
    }


def _parse_dict(report):
    """解析 dict 报告：v1 行为列表或 v2 分节结构。"""
    sample_id = report.get("id")
    if not isinstance(sample_id, str) or not sample_id:
        raise ReportParseError("dict 报告缺少字符串类型的 id 字段", report)

    static = dict(report.get("static") or {})
    if not isinstance(static, dict):
        raise ReportParseError("static 字段必须是对象", report)

    if "behaviors" in report:
        raw_entries = report.get("behaviors")
        if not isinstance(raw_entries, list):
            raise ReportParseError("behaviors 字段必须是列表", report)
        entries = [_normalize_entry(raw, idx) for idx, raw in enumerate(raw_entries)]
        return {
            "id": sample_id,
            "format": "dict-behaviors",
            "entries": entries,
            "static": static,
        }

    if "sections" in report:
        sections = report.get("sections")
        if not isinstance(sections, dict):
            raise ReportParseError("sections 字段必须是对象", report)
        unknown = set(sections) - set(CANONICAL_TYPES)
        if unknown:
            raise ReportParseError(
                f"sections 含未知分节: {sorted(unknown)}", report
            )
        entries = []
        index = 0
        for behavior_type in CANONICAL_TYPES:
            for raw in sections.get(behavior_type, []) or []:
                entry = _normalize_entry(raw, index)
                # 分节位置即行为类型，补齐条目缺失的 type。
                entry["type"] = behavior_type
                entries.append(entry)
                index += 1
        return {
            "id": sample_id,
            "format": "dict-sections",
            "entries": entries,
            "static": static,
        }

    raise ReportParseError(
        "dict 报告必须包含 behaviors 或 sections 字段", report
    )


def parse_raw(report):
    """把任意来源的报告解析为内部标准结构。"""
    if isinstance(report, str):
        text = report.strip()
        if not text:
            raise ReportParseError("报告为空字符串", report)
        try:
            return _parse_text(text)
        except ReportParseError:
            raise
    if isinstance(report, dict):
        return _parse_dict(report)
    raise ReportParseError(
        f"无法识别的报告类型: {type(report).__name__}", report
    )
