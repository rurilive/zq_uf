"""日志格式：可扩展注册表与两种内置格式。"""

import re

from .tokenize import TokenizeError, tokenize
from .timeutil import to_utc


class ParseError(ValueError):
    """单行解析失败，消息会作为失败原因保留。"""


class LogFormat:
    """格式基类：match 判行首特征，parse 返回记录或抛 ParseError。"""

    name = "base"

    def match(self, line):
        raise NotImplementedError

    def parse(self, line):
        raise NotImplementedError


class PlainFormat(LogFormat):
    """如 2026-03-01T10:00:00+08:00 LEVEL 消息，时间可省略到日期。"""

    name = "plain"
    _prefix = re.compile(r"^\d{4}-\d{2}-\d{2}")
    _line = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}"
        r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?"
        r"(?:\s?(?:[+-]\d{2}:?\d{2}|Z))?)?)"
        r"\s+(?P<level>\S+)\s+(?P<msg>.*)$"
    )

    def match(self, line):
        return bool(self._prefix.match(line))

    def parse(self, line):
        matched = self._line.match(line)
        if matched is None:
            raise ParseError("expected <time> <level> <message>")
        try:
            ts = to_utc(matched.group("ts"))
        except ValueError:
            raise ParseError("bad timestamp: %s" % matched.group("ts"))
        try:
            text = " ".join(tokenize(matched.group("msg")))
        except TokenizeError as exc:
            raise ParseError(str(exc))
        return {
            "time": ts,
            "level": matched.group("level"),
            "text": text,
            "format": self.name,
        }


class BracketFormat(LogFormat):
    """如 [2026-03-01 10:00:00 +0800] LEVEL key=value 消息，kv 进 fields。"""

    name = "bracket"

    def match(self, line):
        return line.startswith("[")

    def parse(self, line):
        end = line.find("]")
        if end < 0:
            raise ParseError("missing closing ]")
        try:
            ts = to_utc(line[1:end])
        except ValueError:
            raise ParseError("bad timestamp: %s" % line[1:end].strip())
        try:
            tokens = tokenize(line[end + 1:].strip())
        except TokenizeError as exc:
            raise ParseError(str(exc))
        if not tokens:
            raise ParseError("expected level after timestamp")
        fields = {}
        text_parts = []
        for token in tokens[1:]:
            key, sep, value = token.partition("=")
            if sep and key:
                fields[key] = value
            else:
                text_parts.append(token)
        record = {
            "time": ts,
            "level": tokens[0],
            "text": " ".join(text_parts),
            "format": self.name,
        }
        if fields:
            record["fields"] = fields
        return record


FORMATS = [PlainFormat(), BracketFormat()]


def register_format(fmt, first=False):
    """注册自定义格式；first=True 时优先于内置格式匹配。"""
    if first:
        FORMATS.insert(0, fmt)
    else:
        FORMATS.append(fmt)
    return fmt
