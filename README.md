# logparse

把采集服务日志解析为结构化记录，纯 Python 标准库。

## 内置格式（按行首特征自动识别）

- plain：2026-03-01[T ]10:00:00[+08:00] LEVEL 消息 —— 行首是日期，时间可省略到日期，无时区按 UTC。
- bracket：[2026-03-01 10:00:00 +0800] LEVEL key="value" 消息 —— 行首是 [，kv 对进 fields。

规则：双引号包裹的字段保留空白，支持 \" \\ \n 转义；不匹配任何行首的堆栈行
（缩进、Traceback、XxxError 等）归并到上一条记录的 text；时间统一输出 UTC ISO 8601。

## 接口

- parse(lines) -> 成功记录列表，键为 time/level/text/format（bracket 另含 fields）。
- parse_result(lines) -> ParseResult(records, failures, stats)；failures 保留
  原始行 raw 和失败原因 reason，stats 含 total/parsed/failed/merged 及分格式计数。

## 扩展新格式

继承 LogFormat，实现 match(line)（行首特征）和 parse(line)（失败抛 ParseError），
然后 register_format(fmt, first=False) 注册；时间归一化可复用 to_utc。

    python3 demo.py
    python3 -m unittest discover -s tests
