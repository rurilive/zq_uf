"""用标准库实现的极简 SQL 解析器，只支持 INSERT INTO ... SELECT ... FROM ...。

支持：多表 FROM / JOIN ... ON ...、列别名（AS 或隐式）、函数调用、
四则运算与比较、CASE WHEN、窗口函数 OVER 子句、字面量、CAST。
不支持（显式抛 SQLParseError，不静默跳过）：select *、t.*、子查询、
UNION/INTERSECT/EXCEPT、多条语句、INSERT INTO ... SELECT 以外的语句。
"""

import re
from collections import namedtuple

from .errors import SQLParseError

KEYWORDS = {
    "ALL", "AND", "AS", "ASC", "BETWEEN", "BY", "CASE", "CROSS", "DESC",
    "DISTINCT", "ELSE", "END", "EXCEPT", "FROM", "FULL", "GROUP", "HAVING",
    "IN", "INNER", "INSERT", "INTERSECT", "INTO", "IS", "JOIN", "LEFT",
    "LIKE", "LIMIT", "NOT", "NULL", "ON", "OR", "ORDER", "OUTER", "OVER",
    "PARTITION", "RIGHT", "SELECT", "THEN", "UNION", "VALUES", "WHEN",
    "WHERE", "WINDOW",
}

# CAST 等类型转换里的类型名不是列引用。
TYPE_NAMES = {
    "INT", "INTEGER", "BIGINT", "SMALLINT", "DECIMAL", "NUMERIC", "FLOAT",
    "DOUBLE", "REAL", "VARCHAR", "CHAR", "TEXT", "STRING", "DATE",
    "TIMESTAMP", "DATETIME", "BOOLEAN", "BOOL",
}

Token = namedtuple("Token", ["kind", "value", "start", "end"])

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<comment>--[^\n]*|/\*.*?\*/)
    | (?P<string>'(?:[^']|'')*')
    | (?P<number>\d+(?:\.\d+)?)
    | (?P<qident>"(?:[^"]|"")*")
    | (?P<ident>[A-Za-z_][A-Za-z0-9_$]*)
    | (?P<op><=|>=|<>|!=|\|\||[(),.+\-*/%=<>;])
    """,
    re.VERBOSE | re.DOTALL,
)

_CLAUSE_KEYWORDS = {"WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "WINDOW"}
_JOIN_KEYWORDS = {"JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS"}
_UNSUPPORTED_KEYWORDS = {"UNION", "INTERSECT", "EXCEPT"}


def tokenize(sql):
    tokens = []
    pos = 0
    while pos < len(sql):
        match = _TOKEN_RE.match(sql, pos)
        if match is None:
            raise SQLParseError("无法识别的字符（位置 %d）: %r" % (pos, sql[pos:pos + 20]))
        pos = match.end()
        kind = match.lastgroup
        if kind in ("ws", "comment"):
            continue
        value = match.group()
        if kind == "ident" and value.upper() in KEYWORDS:
            kind, value = "kw", value.upper()
        elif kind == "qident":
            kind, value = "ident", value[1:-1].replace('""', '"')
        tokens.append(Token(kind, value, match.start(), match.end()))
    return tokens


def parse_insert_select(sql):
    """解析一条 INSERT INTO ... SELECT ... 语句。

    返回 {"target": 表名, "sources": {别名: 表名}, "items": [选择项]}；
    选择项为 {"expression", "columns": [(限定符|None, 列名)], "functions",
    "alias", "lone_column"}。
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SQLParseError("sql 必须是非空字符串")
    tokens = tokenize(sql)
    if tokens and tokens[-1].value == ";":
        tokens.pop()
    pos = _expect_keyword(tokens, 0, "INSERT")
    pos = _expect_keyword(tokens, pos, "INTO")
    target, pos = _parse_table_name(tokens, pos)
    pos = _expect_keyword(tokens, pos, "SELECT")
    select_end = _find_select_end(tokens, pos)
    items = _parse_select_items(tokens[pos:select_end], sql)
    sources, pos = _parse_sources(tokens, select_end + 1)
    _check_tail(tokens, pos)
    return {"target": target, "sources": sources, "items": items}


def _expect_keyword(tokens, pos, keyword):
    if pos >= len(tokens) or tokens[pos].kind != "kw" or tokens[pos].value != keyword:
        got = tokens[pos].value if pos < len(tokens) else "结尾"
        raise SQLParseError(
            "期望关键字 %s，实际为 %r；只支持 INSERT INTO ... SELECT ... 语句" % (keyword, got))
    return pos + 1


def _parse_table_name(tokens, pos):
    if pos >= len(tokens) or tokens[pos].kind != "ident":
        got = tokens[pos].value if pos < len(tokens) else "结尾"
        raise SQLParseError("期望表名，实际为 %r" % got)
    name = tokens[pos].value
    pos += 1
    if pos + 1 < len(tokens) and tokens[pos].value == "." and tokens[pos + 1].kind == "ident":
        name += "." + tokens[pos + 1].value
        pos += 2
    return name, pos


def _find_select_end(tokens, pos):
    depth = 0
    i = pos
    while i < len(tokens):
        tok = tokens[i]
        if tok.value == "(":
            depth += 1
        elif tok.value == ")":
            depth -= 1
            if depth < 0:
                raise SQLParseError("括号不匹配（位置 %d）" % tok.start)
        elif tok.kind == "kw":
            if tok.value == "SELECT":
                raise SQLParseError("不支持子查询，请拆成多个任务")
            if tok.value == "FROM" and depth == 0:
                if i == pos:
                    raise SQLParseError("SELECT 后缺少选择项")
                return i
        i += 1
    raise SQLParseError("缺少 FROM 子句")


def _check_tail(tokens, pos):
    depth = 0
    for tok in tokens[pos:]:
        if tok.value == "(":
            depth += 1
        elif tok.value == ")":
            depth -= 1
            if depth < 0:
                raise SQLParseError("括号不匹配（位置 %d）" % tok.start)
        if tok.kind == "kw":
            if tok.value == "SELECT":
                raise SQLParseError("不支持子查询，请拆成多个任务")
            if tok.value in _UNSUPPORTED_KEYWORDS:
                raise SQLParseError("不支持 %s，请拆成多个任务" % tok.value)
            if tok.value in ("INSERT", "VALUES"):
                raise SQLParseError("只支持单条 INSERT INTO ... SELECT 语句")
    if depth != 0:
        raise SQLParseError("括号不匹配")


def _parse_sources(tokens, pos):
    sources = {}
    expect_table = True
    i = pos
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "kw" and tok.value in _CLAUSE_KEYWORDS | _UNSUPPORTED_KEYWORDS:
            break
        if expect_table:
            table, i = _parse_table_name(tokens, i)
            alias, i = _parse_table_alias(tokens, i)
            sources[alias or table] = table
            expect_table = False
            continue
        if tok.value == ",":
            expect_table = True
            i += 1
            continue
        if tok.kind == "kw" and tok.value in _JOIN_KEYWORDS:
            while i < len(tokens) and not (tokens[i].kind == "kw" and tokens[i].value == "JOIN"):
                i += 1
            i += 1  # 跳过 JOIN
            table, i = _parse_table_name(tokens, i)
            alias, i = _parse_table_alias(tokens, i)
            sources[alias or table] = table
            i = _skip_join_condition(tokens, i)
            continue
        raise SQLParseError("FROM 子句中出现意外的记号 %r（位置 %d）" % (tok.value, tok.start))
    if expect_table:
        raise SQLParseError("FROM 子句缺少表名")
    if not sources:
        raise SQLParseError("缺少来源表")
    return sources, i


def _parse_table_alias(tokens, pos):
    if pos < len(tokens) and tokens[pos].kind == "kw" and tokens[pos].value == "AS":
        pos += 1
        if pos >= len(tokens) or tokens[pos].kind != "ident":
            raise SQLParseError("AS 后需要表别名")
        return tokens[pos].value, pos + 1
    if pos < len(tokens) and tokens[pos].kind == "ident":
        return tokens[pos].value, pos + 1
    return None, pos


def _skip_join_condition(tokens, pos):
    if pos >= len(tokens) or not (tokens[pos].kind == "kw" and tokens[pos].value == "ON"):
        return pos
    pos += 1
    depth = 0
    while pos < len(tokens):
        tok = tokens[pos]
        if depth == 0 and (tok.value == "," or (tok.kind == "kw" and tok.value in (
                _CLAUSE_KEYWORDS | _JOIN_KEYWORDS | _UNSUPPORTED_KEYWORDS))):
            break
        if tok.kind == "kw" and tok.value == "SELECT":
            raise SQLParseError("不支持子查询，请拆成多个任务")
        if tok.value == "(":
            depth += 1
        elif tok.value == ")":
            if depth == 0:
                raise SQLParseError("括号不匹配（位置 %d）" % tok.start)
            depth -= 1
        pos += 1
    return pos


def _parse_select_items(tokens, sql):
    if not tokens:
        raise SQLParseError("SELECT 后缺少选择项")
    groups = []
    current = []
    depth = 0
    for tok in tokens:
        if tok.value == "(":
            depth += 1
        elif tok.value == ")":
            depth -= 1
            if depth < 0:
                raise SQLParseError("括号不匹配（位置 %d）" % tok.start)
        if tok.value == "," and depth == 0:
            groups.append(current)
            current = []
        else:
            current.append(tok)
    if depth != 0:
        raise SQLParseError("括号不匹配")
    groups.append(current)
    items = []
    for group in groups:
        if not group:
            raise SQLParseError("选择项为空（多余的逗号？）")
        items.append(_parse_select_item(group, sql))
    return items


def _parse_select_item(tokens, sql):
    expr_tokens, alias = _split_alias(tokens)
    if not expr_tokens:
        raise SQLParseError("选择项为空")
    columns = []
    functions = []
    i = 0
    while i < len(expr_tokens):
        tok = expr_tokens[i]
        nxt = expr_tokens[i + 1] if i + 1 < len(expr_tokens) else None
        if tok.kind == "ident":
            upper = tok.value.upper()
            if upper in TYPE_NAMES:
                i += 1
                continue
            if nxt is not None and nxt.value == "(":
                functions.append(upper)
                i += 1
                continue
            if nxt is not None and nxt.value == ".":
                if i + 2 < len(expr_tokens) and expr_tokens[i + 2].kind == "ident":
                    columns.append((tok.value, expr_tokens[i + 2].value))
                    i += 3
                    continue
                if i + 2 < len(expr_tokens) and expr_tokens[i + 2].value == "*":
                    raise SQLParseError("不支持 %s.*，请显式列出字段" % tok.value)
                raise SQLParseError("无法解析的列引用（位置 %d）" % tok.start)
            columns.append((None, tok.value))
            i += 1
            continue
        if tok.kind == "kw":
            if tok.value == "SELECT":
                raise SQLParseError("不支持子查询，请拆成多个任务")
            i += 1
            continue
        if tok.value == "*" and i == 0:
            raise SQLParseError("select * 无法展开字段，请显式列出")
        i += 1
    columns = list(dict.fromkeys(columns))
    functions = list(dict.fromkeys(functions))
    lone_column = (
        len(columns) == 1 and not functions
        and len(expr_tokens) == (3 if columns[0][0] else 1)
    )
    expression = sql[expr_tokens[0].start:expr_tokens[-1].end]
    return {"expression": expression, "columns": columns, "functions": functions,
            "alias": alias, "lone_column": lone_column}


def _split_alias(tokens):
    depth = 0
    for idx, tok in enumerate(tokens):
        if tok.value == "(":
            depth += 1
        elif tok.value == ")":
            depth -= 1
        elif depth == 0 and tok.kind == "kw" and tok.value == "AS":
            if idx + 1 != len(tokens) - 1 or tokens[idx + 1].kind != "ident":
                raise SQLParseError("AS 后需要且仅需要一个别名")
            return tokens[:idx], tokens[idx + 1].value
    last = tokens[-1]
    if last.kind == "ident" and len(tokens) > 1:
        prev = tokens[-2]
        if prev.value == ")" or prev.kind in ("ident", "string", "number"):
            return tokens[:-1], last.value
        if prev.kind == "kw" and prev.value == "END":
            return tokens[:-1], last.value
    return tokens, None

