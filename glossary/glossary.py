"""术语和字段的映射中心。

术语记录定义、别名、缩写、口径、负责人、来源和有效期；
字段可挂多个术语，术语可跨表；改名保留历史，冲突保留来源。
"""

_terms = {}          # name -> 当前术语记录
_history = {}        # name -> 历史版本列表（旧定义、旧名字）
_fields = {}         # (table, column) -> {"owner": ..., "terms": [...]}
_conflicts = []      # 冲突记录，保留双方来源
_notifications = []  # 术语变更时发给字段负责人的通知

_TERM_KEYS = (
    "definition", "aliases", "abbreviations", "caliber",
    "owner", "source", "valid_from", "valid_to",
)


def reset():
    _terms.clear()
    _history.clear()
    _fields.clear()
    _conflicts.clear()
    _notifications.clear()


def _record(term):
    record = {"name": term["name"]}
    for key in _TERM_KEYS:
        value = term.get(key)
        if key in ("aliases", "abbreviations"):
            value = list(value or [])
        record[key] = value
    return record


def _notify(term_name, change):
    for (table, column), field in _fields.items():
        if term_name in field["terms"] and field["owner"]:
            _notifications.append({
                "term": term_name,
                "field": table + "." + column,
                "owner": field["owner"],
                "change": change,
            })


def define(term):
    """登记或更新术语；旧版本进历史，来源不同的口径冲突保留双方来源。"""
    name = term["name"]
    record = _record(term)
    old = _terms.get(name)
    if old is not None:
        _history.setdefault(name, []).append(old)
        if (old["definition"] != record["definition"]
                and old["source"] != record["source"]):
            _conflicts.append({
                "name": name,
                "kept": {"definition": record["definition"],
                         "source": record["source"]},
                "previous": {"definition": old["definition"],
                             "source": old["source"]},
            })
        _notify(name, "redefined")
    _terms[name] = record
    return dict(record)


def lookup(name):
    """按精确术语名查询，查不到返回None；别名请用search。"""
    record = _terms.get(name)
    return dict(record) if record is not None else None


def map_field(table, column, term, owner=None):
    """把字段挂到术语上；一个字段可挂多个术语，一个术语可跨表。"""
    if term not in _terms:
        raise KeyError("undefined term: " + term)
    key = (table, column)
    field = _fields.setdefault(key, {"owner": None, "terms": []})
    if owner is not None:
        field["owner"] = owner
    if term not in field["terms"]:
        field["terms"].append(term)
    return {"table": table, "column": column,
            "owner": field["owner"], "terms": list(field["terms"])}


def fields_of(term):
    """术语映射到的所有字段，格式table.column。"""
    return sorted(table + "." + column
                  for (table, column), field in _fields.items()
                  if term in field["terms"])


def terms_of(table, column):
    """字段上挂的所有术语名。"""
    field = _fields.get((table, column))
    return list(field["terms"]) if field else []


def rename(old_name, new_name):
    """术语改名，旧名字和旧定义留在历史里，字段映射同步到新名。"""
    record = _terms.pop(old_name)
    prior = _history.pop(old_name, [])
    prior.append(record)
    _history.setdefault(new_name, []).extend(prior)
    renamed = dict(record, name=new_name)
    _terms[new_name] = renamed
    for field in _fields.values():
        field["terms"] = [new_name if t == old_name else t
                          for t in field["terms"]]
    _notify(new_name, "renamed from " + old_name)
    return dict(renamed)


def history(name):
    """按时间顺序返回旧版本（含旧定义和改名前的记录）。"""
    return [dict(r) for r in _history.get(name, [])]


def conflicts():
    """口径冲突记录，kept/previous各自带来源。"""
    return [dict(c) for c in _conflicts]


def notifications(owner=None):
    """术语变更通知；传owner只看他负责字段的通知。"""
    result = [dict(n) for n in _notifications]
    if owner is not None:
        result = [n for n in result if n["owner"] == owner]
    return result


def _snippet(text, query, width=24):
    idx = text.lower().find(query.lower())
    if idx < 0:
        return text
    start = max(0, idx - width // 2)
    end = min(len(text), idx + len(query) + width // 2)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end] + suffix


def search(query):
    """按名字、别名、缩写和定义描述搜索，结果带匹配片段。"""
    results = []
    for record in _terms.values():
        candidates = [("name", record["name"]),
                      ("definition", record["definition"] or ""),
                      ("caliber", record["caliber"] or "")]
        candidates += [("alias", a) for a in record["aliases"]]
        candidates += [("abbreviation", a) for a in record["abbreviations"]]
        for kind, text_value in candidates:
            if query.lower() in text_value.lower():
                results.append({
                    "name": record["name"],
                    "matched": kind,
                    "snippet": _snippet(text_value, query),
                    "term": dict(record),
                })
                break
    return results
