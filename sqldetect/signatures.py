"""按数据库类型分开的报错特征表。

同一条响应可能同时命中多个数据库的特征，匹配函数会返回按置信度排序的候选，
由上层取最高的一条，避免把 MySQL 的报错记成 PostgreSQL。
"""

import re

# 片段太短、容易落进普通单词里（如 ora- 命中 aurora-borealis），
# 这类片段改为整词/带边界匹配。
WORD_BOUNDED = frozenset({"ora-"})

# 每条特征: (响应中应出现的片段, 置信度)。片段统一用大写书写，匹配前整体转小写。
DB_SIGNATURES = {
    "mysql": (
        ("you have an error in your sql syntax", 0.97),
        ("check the manual that corresponds to your mysql server version", 0.97),
        ("warning: mysql", 0.92),
        ("mysql_fetch", 0.91),
        ("mysqli_sql_exception", 0.94),
        ("mysqlexception", 0.93),
        ("mysql server version", 0.9),
        ("mariadb", 0.88),
    ),
    "postgresql": (
        ("pg::syntaxerror", 0.96),
        ("postgresql", 0.94),
        ("pg_query", 0.92),
        ("pg_exec", 0.92),
        ("syntax error at or near", 0.92),
        ("unterminated quoted string at or near", 0.94),
        ("invalid input syntax for", 0.9),
        ("psql:", 0.9),
    ),
    "mssql": (
        ("microsoft ole db provider for sql server", 0.97),
        ("unclosed quotation mark after the character string", 0.97),
        ("system.data.sqlclient", 0.95),
        ("odbc sql server driver", 0.93),
        ("incorrect syntax near", 0.92),
        ("microsoft sql server", 0.91),
        ("sqlserver jdbc driver", 0.9),
        ("conversion failed when converting the", 0.85),
    ),
    "oracle": (
        ("ora-", 0.96),
        ("quoted string not properly terminated", 0.94),
        ("oracle.jdbc", 0.92),
        ("oracle.net", 0.9),
        ("oracle error", 0.9),
    ),
    "sqlite": (
        ("sqlite3::", 0.95),
        ("sqliteexception", 0.94),
        ("sqlite error", 0.93),
        ("unrecognized token:", 0.9),
        ("sqlite_", 0.88),
    ),
    "access": (
        ("microsoft access driver", 0.93),
        ("microsoft jet database engine", 0.93),
    ),
    "db2": (
        ("db2 sql error", 0.93),
        ("com.ibm.db2", 0.93),
    ),
}

# 只说明"页面在报错"，不足以归到某个数据库。命中这些片段时只标待确认。
GENERIC_SIGNATURES = (
    "sql syntax",
    "sqlstate",
    "unknown column",
    "database error",
    "sql error",
    "syntax error",
    "odbc",
    "jdbc",
)


def _contains(text, phrase):
    """片段匹配。短片段要求整词边界，避免命中普通单词内部。"""
    if phrase not in WORD_BOUNDED:
        return phrase in text
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase), text) is not None


def match_error_signatures(body):
    """返回命中的数据库报错特征，按置信度从高到低排序。

    每种数据库只保留一条最具体的特征；命中多条时小幅提升置信度。
    """
    text = str(body or "").lower()
    if not text:
        return []
    matched = []
    for dbms, signatures in DB_SIGNATURES.items():
        best = None
        hits = 0
        for phrase, score in signatures:
            if _contains(text, phrase):
                hits += 1
                if best is None or (score, len(phrase)) > (best[1], len(best[0])):
                    best = (phrase, score)
        if best is not None:
            confidence = min(0.99, best[1] + 0.01 * min(hits - 1, 3))
            matched.append({
                "dbms": dbms,
                "signature": best[0],
                "confidence": round(confidence, 3),
                "hits": hits,
            })
    matched.sort(key=lambda item: (-item["confidence"], -len(item["signature"]), item["dbms"]))
    return matched


def generic_error_signature(body):
    """返回命中的通用报错片段，没有则返回 None。"""
    text = str(body or "").lower()
    for phrase in GENERIC_SIGNATURES:
        if phrase in text:
            return phrase
    return None
