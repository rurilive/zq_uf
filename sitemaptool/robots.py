"""robots.txt 规则应用：与规则冲突的链接默认排除并记录。"""
from __future__ import annotations

import logging
import urllib.robotparser
import urllib.request
from urllib.parse import quote, urlsplit

from .priority import site_of

logger = logging.getLogger("sitemaptool")


def build_robots(rules=None, *, auto_discover=False, user_agent="*", timeout=10):
    """构造 {站点: RobotFileParser}。

    rules 的键为站点（host[:port]），值为 robots 文本或本地文件路径。
    auto_discover=True 时对 http(s) 站点尝试拉取 https?://host/robots.txt，
    拉取失败则默认全部放行。
    """
    parsers = {}
    for host, rule in (rules or {}).items():
        parser = urllib.robotparser.RobotFileParser()
        lines = _rule_lines(rule)
        parser.parse(lines)
        parsers[host] = parser
    if auto_discover:
        for host in parsers:
            _discover_host(parsers, host, timeout)
    return parsers


def _rule_lines(rule):
    if rule and isinstance(rule, str) and "\n" not in rule and _is_path(rule):
        with open(rule, encoding="utf-8") as handle:
            return handle.read().splitlines()
    return rule.splitlines() if isinstance(rule, str) else list(rule)


def _is_path(value):
    return value.startswith((".", "/")) or ":" not in value


def _discover_host(parsers, host, timeout):
    for scheme in ("https", "http"):
        url = f"{scheme}://{host}/robots.txt"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                lines = response.read().decode("utf-8", "replace").splitlines()
        except Exception as exc:
            logger.info("robots 自动发现失败，默认放行：%s（%s）", host, exc)
            continue
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(url)
        parser.parse(lines)
        parsers[host] = parser
        return


def apply_robots(entries, parsers=None, *, user_agent="*"):
    """按站点应用 robots 规则，返回 (allowed, excluded)。"""
    allowed = []
    excluded = []
    for entry in entries:
        loc = entry.get("loc", "")
        parser = (parsers or {}).get(site_of(loc))
        if parser is not None and not _can_fetch(parser, loc, user_agent):
            reason = _disallow_reason(parser, loc)
            logger.info("robots 冲突，排除链接：%s（%s）", loc, reason)
            excluded.append({"loc": loc, "reason": reason})
        else:
            allowed.append(entry)
    return allowed, excluded


def _can_fetch(parser, loc, user_agent):
    try:
        return parser.can_fetch(user_agent, loc)
    except Exception:
        return urlsplit(loc).scheme not in ("http", "https", "")


def _disallow_reason(parser, loc):
    path = quote(urlsplit(loc).path or "/")
    for pattern in _disallow_lines(parser, path):
        return f"Disallow: {pattern}"
    return "robots Disallow"


def _disallow_lines(parser, path):
    rules = list(getattr(parser, "entries", None) or [])
    default = getattr(parser, "default_entry", None)
    if default is not None:
        rules.append(default)
    matches = []
    for rule_entry in rules:
        for pattern in rule_entry.rulelines:
            if pattern.applies_to(path) and pattern.allowance is False:
                matches.append(pattern.path)
    return matches
