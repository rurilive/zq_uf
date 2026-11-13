"""数据目录搜索：索引表名、描述、字段和标签，支持同义词、前缀匹配和相关度排序。"""

import math
import re

# --- 可调参数（详见 README）---
MIN_PREFIX_LEN = 2           # 触发前缀匹配的最小词长
NAME_WEIGHT = 4.0            # 表名命中权重
TAG_WEIGHT = 3.0             # 标签命中权重
FIELD_WEIGHT = 2.0           # 字段名命中权重
DESC_WEIGHT = 1.0            # 描述命中权重
EXACT_FACTOR = 1.0           # 精确命中系数
PREFIX_FACTOR = 0.6          # 前缀命中系数
SYNONYM_EXACT_FACTOR = 0.8   # 同义词精确命中系数
SYNONYM_PREFIX_FACTOR = 0.5  # 同义词前缀命中系数
POPULARITY_WEIGHT = 0.1      # 热度系数，得分加上 log1p(popularity) * 该值

# 同义词与缩写表（双向），查询词精确等于键时展开
SYNONYMS = {
    "订单": ("order",), "order": ("订单",),
    "用户": ("user",), "user": ("用户",),
    "销售": ("sales",), "sales": ("销售",),
    "城市": ("city",), "city": ("城市",),
    "金额": ("amount",), "amount": ("金额",),
    "成交额": ("gmv",), "gmv": ("成交额",),
}

# 中文分词词表（最长匹配，长词在前）；未登录字逐字成词
_CJK_VOCAB = (
    "成交额",
    "订单", "交易", "明细", "用户", "维表", "销售",
    "汇总", "城市", "金额", "支付", "流水",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")

TABLES = [
    {"name": "dwd_orders", "desc": "订单明细", "fields": ["order_id", "amount"]},
    {"name": "dim_user", "desc": "用户维表", "fields": ["user_id", "city"]},
    {"name": "dws_sales", "desc": "销售汇总", "fields": ["order_id", "gmv"]},
]

_DEFAULT_TAGS = {
    "dwd_orders": ("trade", "order"),
    "dim_user": ("user",),
    "dws_sales": ("sales",),
}
_DEFAULT_POPULARITY = {"dwd_orders": 120, "dim_user": 60, "dws_sales": 200}


def _segment_cjk(run):
    tokens = []
    index = 0
    while index < len(run):
        for word in _CJK_VOCAB:
            if run.startswith(word, index):
                tokens.append(word)
                index += len(word)
                break
        else:
            tokens.append(run[index])
            index += 1
    return tokens


def _tokenize(text):
    """英文按下划线切词并保留原串，中文按词表最长匹配切词。"""
    tokens = []
    for run in _TOKEN_RE.findall(text):
        if run.isascii():
            tokens.append(run)
            tokens.extend(part for part in run.split("_") if part and part != run)
        else:
            tokens.extend(_segment_cjk(run))
    return tokens


def _parse_query(query):
    if not isinstance(query, str):
        raise TypeError("query must be str, got %s" % type(query).__name__)
    terms = query.split()
    if not terms:
        raise ValueError("query must be a non-empty string")
    return terms


def _match_score(term, tokens, weight):
    """单词对一组 token 的最优得分：精确 > 前缀 > 同义词。区分大小写。"""
    best = 0.0
    for token in tokens:
        if token == term:
            best = max(best, weight * EXACT_FACTOR)
        elif len(term) >= MIN_PREFIX_LEN and token.startswith(term):
            best = max(best, weight * PREFIX_FACTOR)
    for synonym in SYNONYMS.get(term, ()):
        for token in tokens:
            if token == synonym:
                best = max(best, weight * SYNONYM_EXACT_FACTOR)
            elif len(synonym) >= MIN_PREFIX_LEN and token.startswith(synonym):
                best = max(best, weight * SYNONYM_PREFIX_FACTOR)
    return best


def _validate_text(value, label, allow_empty):
    if not isinstance(value, str):
        raise TypeError("%s must be str, got %s" % (label, type(value).__name__))
    if not allow_empty and not value.strip():
        raise ValueError("%s must be a non-empty string" % label)
    return value


class SearchIndex:
    """可序列化的搜索索引；add_table 建索引，search/search_detail 查询。"""

    VERSION = 1

    def __init__(self):
        self._tables = []

    def add_table(self, name, desc, fields, tags=(), popularity=0):
        _validate_text(name, "table name", allow_empty=False)
        _validate_text(desc, "desc", allow_empty=True)
        if not isinstance(fields, (list, tuple)) or not fields:
            raise ValueError("fields must be a non-empty list of field names")
        for field in fields:
            _validate_text(field, "field name", allow_empty=False)
        if not isinstance(tags, (list, tuple)):
            raise TypeError("tags must be a list or tuple of str")
        for tag in tags:
            _validate_text(tag, "tag", allow_empty=False)
        if isinstance(popularity, bool) or not isinstance(popularity, (int, float)):
            raise TypeError("popularity must be a number")
        if popularity < 0:
            raise ValueError("popularity must be >= 0")
        if any(entry["name"] == name for entry in self._tables):
            raise ValueError("duplicate table: %s" % name)
        self._tables.append({
            "name": name,
            "desc": desc,
            "fields": list(fields),
            "tags": list(tags),
            "popularity": popularity,
            "_name_tokens": _tokenize(name),
            "_desc_tokens": _tokenize(desc),
            "_field_tokens": {field: _tokenize(field) for field in fields},
            "_tag_tokens": {tag: _tokenize(tag) for tag in tags},
        })
        return self

    def _locations(self, entry):
        yield NAME_WEIGHT, "name", entry["name"], entry["_name_tokens"]
        for tag, tokens in entry["_tag_tokens"].items():
            yield TAG_WEIGHT, "tag", tag, tokens
        for field, tokens in entry["_field_tokens"].items():
            yield FIELD_WEIGHT, "field", field, tokens
        yield DESC_WEIGHT, "desc", entry["desc"], entry["_desc_tokens"]

    def _search(self, query):
        terms = _parse_query(query)
        results = []
        for entry in self._tables:
            total = 0.0
            snippets = []
            for term in terms:
                term_best = 0.0
                for weight, where, text, tokens in self._locations(entry):
                    score = _match_score(term, tokens, weight)
                    if score > 0:
                        snippets.append({"where": where, "text": text, "term": term})
                        term_best = max(term_best, score)
                if term_best == 0.0:
                    break
                total += term_best
            else:
                total += POPULARITY_WEIGHT * math.log1p(entry["popularity"])
                results.append((entry, total, snippets))
        results.sort(key=lambda item: (-item[1], -item[0]["popularity"], item[0]["name"]))
        return results

    @staticmethod
    def _public(entry):
        return {"name": entry["name"], "desc": entry["desc"], "fields": list(entry["fields"])}

    def search(self, query):
        """返回命中表（仅 name/desc/fields 三键），按相关度与热度排序。"""
        return [self._public(entry) for entry, _, _ in self._search(query)]

    def search_detail(self, query):
        """返回命中表、得分和命中片段。"""
        return [
            {"table": self._public(entry), "score": round(score, 6), "snippets": snippets}
            for entry, score, snippets in self._search(query)
        ]

    def to_dict(self):
        """导出为可 JSON 序列化的 dict。"""
        return {
            "version": self.VERSION,
            "tables": [
                {
                    "name": entry["name"],
                    "desc": entry["desc"],
                    "fields": list(entry["fields"]),
                    "tags": list(entry["tags"]),
                    "popularity": entry["popularity"],
                }
                for entry in self._tables
            ],
        }

    @classmethod
    def from_dict(cls, data):
        """从 to_dict 的导出结果重建索引，搜索结果与重建前一致。"""
        if not isinstance(data, dict):
            raise TypeError("index data must be a dict, got %s" % type(data).__name__)
        if data.get("version") != cls.VERSION:
            raise ValueError("unsupported index version: %r" % data.get("version"))
        tables = data.get("tables")
        if not isinstance(tables, list):
            raise ValueError("index data must contain a 'tables' list")
        index = cls()
        for item in tables:
            if not isinstance(item, dict):
                raise ValueError("table entry must be a dict, got %r" % (item,))
            missing = {"name", "desc", "fields"} - set(item)
            if missing:
                raise ValueError("table entry missing keys: %s" % sorted(missing))
            index.add_table(
                item["name"], item["desc"], item["fields"],
                tags=item.get("tags", ()), popularity=item.get("popularity", 0),
            )
        return index


def _build_default_index():
    index = SearchIndex()
    for table in TABLES:
        index.add_table(
            table["name"], table["desc"], table["fields"],
            tags=_DEFAULT_TAGS.get(table["name"], ()),
            popularity=_DEFAULT_POPULARITY.get(table["name"], 0),
        )
    return index


_DEFAULT_INDEX = _build_default_index()


def search(query):
    """在默认索引（TABLES）上搜索，返回命中表（仅 name/desc/fields 三键）。"""
    return _DEFAULT_INDEX.search(query)


def search_detail(query):
    """在默认索引上搜索，返回命中表、得分和命中片段。"""
    return _DEFAULT_INDEX.search_detail(query)
