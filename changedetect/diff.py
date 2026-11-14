"""结构感知的变更判定。

按标签和稳定属性对齐节点，识别移动、插入和删除；忽略脚本、样式和
广告区域；文本变化按字段归并；按内容和结构分级，只有影响业务字段
的变化才入库，其余只记审计。
"""

import re
from html.parser import HTMLParser

__all__ = ["diff", "should_ingest"]

NOISE_TAGS = frozenset({"script", "style", "noscript", "template"})
AD_TOKENS = frozenset(
    {"ad", "ads", "advert", "advertisement", "adslot", "sponsor", "sponsored"}
)
BUSINESS_TOKENS = frozenset(
    {"price", "title", "sku", "stock", "brand", "amount", "product"}
)
CHROME_TOKENS = frozenset(
    {"nav", "footer", "menu", "copyright", "disclaimer", "breadcrumb"}
)
STABLE_ATTRS = ("id", "data-id", "data-key", "name")

_GRADE_RANK = {"none": 0, "layout": 1, "content": 2, "business": 3}
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")


class _Node:
    __slots__ = ("tag", "attrs", "children", "text_parts")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.text_parts = []

    @property
    def text(self):
        return _WS.sub(" ", " ".join(self.text_parts)).strip()

    def full_text(self):
        parts = [self.text] if self.text else []
        for child in self.children:
            sub = child.full_text()
            if sub:
                parts.append(sub)
        return " ".join(parts)


class _Parser(HTMLParser):
    VOID_TAGS = frozenset(
        {"area", "base", "br", "col", "embed", "hr", "img", "input",
         "link", "meta", "source", "track", "wbr"}
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self.stack = [self.root]
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            if tag not in self.VOID_TAGS:
                self.skip_depth += 1
            return
        node = _Node(tag, dict(attrs))
        if tag in self.VOID_TAGS:
            if not _is_ad(node):
                self.stack[-1].children.append(node)
            return
        if tag in NOISE_TAGS or _is_ad(node):
            self.skip_depth = 1
            return
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if self.skip_depth:
            return
        node = _Node(tag, dict(attrs))
        if tag not in NOISE_TAGS and not _is_ad(node):
            self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        if self.skip_depth:
            self.skip_depth -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.stack[-1].text_parts.append(data)


def _parse(html):
    parser = _Parser()
    parser.feed(html)
    parser.close()
    return parser.root


def _tokens(attrs):
    tokens = set()
    for key, value in attrs.items():
        if not isinstance(value, str) or not value:
            continue
        if key in ("class", "id") or key.startswith("data-"):
            tokens.update(t for t in _TOKEN_SPLIT.split(value.lower()) if t)
    return tokens


def _is_ad(node):
    return bool(_tokens(node.attrs) & AD_TOKENS)


def _field_grade(tokens):
    if tokens & CHROME_TOKENS and not tokens & BUSINESS_TOKENS:
        return "content"
    # 业务标记或未标记内容都按业务处理，宁可多入库也不错漏
    return "business"


def _stable_key(node):
    for attr in STABLE_ATTRS:
        value = node.attrs.get(attr)
        if value:
            return (node.tag, attr, value)
    return None


def _own_label(node):
    for key in ("data-field", "id", "class", "name"):
        value = node.attrs.get(key)
        if value:
            return value.split()[0]
    return None


def _segment(node, index):
    if node.attrs.get("id"):
        return "%s#%s" % (node.tag, node.attrs["id"])
    if node.attrs.get("class"):
        return "%s.%s" % (node.tag, node.attrs["class"].split()[0])
    return "%s[%d]" % (node.tag, index)


def _join(path, segment):
    return "%s > %s" % (path, segment) if path else segment


def _lis(seq):
    """最长递增子序列（返回下标），序列之外的节点视为移动。"""
    n = len(seq)
    if not n:
        return []
    dp = [1] * n
    prev = [-1] * n
    best = 0
    for i in range(n):
        for j in range(i):
            if seq[j] < seq[i] and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                prev[i] = j
        if dp[i] > dp[best]:
            best = i
    out = []
    k = best
    while k != -1:
        out.append(k)
        k = prev[k]
    return out[::-1]


def _match(old_kids, new_kids):
    """三轮对齐：稳定属性 -> 标签+全文一致 -> 同标签按序配对。"""
    pairs = []
    used_new = set()
    remaining = set(range(len(old_kids)))

    def claim(i, j):
        pairs.append((i, j))
        used_new.add(j)
        remaining.discard(i)

    by_key = {}
    for j, kid in enumerate(new_kids):
        key = _stable_key(kid)
        if key is not None:
            by_key.setdefault(key, []).append(j)
    for i, kid in enumerate(old_kids):
        key = _stable_key(kid)
        if key is None:
            continue
        for j in by_key.get(key, ()):
            if j not in used_new:
                claim(i, j)
                break

    by_text = {}
    for j, kid in enumerate(new_kids):
        if j not in used_new:
            by_text.setdefault((kid.tag, kid.full_text()), []).append(j)
    for i in sorted(remaining):
        kid = old_kids[i]
        for j in by_text.get((kid.tag, kid.full_text()), ()):
            if j not in used_new:
                claim(i, j)
                break

    by_tag = {}
    for j, kid in enumerate(new_kids):
        if j not in used_new:
            by_tag.setdefault(kid.tag, []).append(j)
    for i in sorted(remaining):
        for j in by_tag.get(old_kids[i].tag, ()):
            if j not in used_new:
                claim(i, j)
                break
    return pairs


class _Result:
    def __init__(self):
        self.fields = []
        self.inserts = 0
        self.deletes = 0
        self.moves = 0
        self.grade = "none"
        self.audit = []

    def record(self, grade, detail):
        if _GRADE_RANK[grade] > _GRADE_RANK[self.grade]:
            self.grade = grade
        if grade != "business":
            self.audit.append("%s: %s" % (grade, detail))


def _compare(old, new, path, label, tokens, res):
    tokens = tokens | _tokens(old.attrs) | _tokens(new.attrs)
    label = _own_label(new) or _own_label(old) or label or new.tag
    if old.text != new.text:
        res.fields.append({
            "field": label,
            "old": old.text,
            "new": new.text,
            "location": path,
        })
        res.record(_field_grade(tokens), "field %s at %s" % (label, path))
    _align(old.children, new.children, path, label, tokens, res)


def _align(old_kids, new_kids, path, label, tokens, res):
    pairs = _match(old_kids, new_kids)
    used_old = {i for i, _ in pairs}
    used_new = {j for _, j in pairs}
    for i, kid in enumerate(old_kids):
        if i not in used_old:
            res.deletes += 1
            res.record(_field_grade(tokens | _tokens(kid.attrs)),
                       "delete %s" % _join(path, _segment(kid, i)))
    for j, kid in enumerate(new_kids):
        if j not in used_new:
            res.inserts += 1
            res.record(_field_grade(tokens | _tokens(kid.attrs)),
                       "insert %s" % _join(path, _segment(kid, j)))
    pairs.sort()
    keep = set(_lis([j for _, j in pairs]))
    for k, (i, j) in enumerate(pairs):
        if k not in keep:
            res.moves += 1
            res.record("layout", "move %s" % _join(path, _segment(new_kids[j], j)))
    for i, j in pairs:
        _compare(old_kids[i], new_kids[j],
                 _join(path, _segment(new_kids[j], j)), label, tokens, res)


def diff(old, new):
    """比较两个页面版本，返回结构化的变更描述。"""
    res = _Result()
    _compare(_parse(old), _parse(new), "$", None, frozenset(), res)
    changed_nodes = res.inserts + res.deletes + res.moves + len(res.fields)
    return {
        "changed": changed_nodes > 0,
        "text_equal": old == new,
        "changed_nodes": changed_nodes,
        "moves": res.moves,
        "inserts": res.inserts,
        "deletes": res.deletes,
        "fields": res.fields,
        "grade": res.grade,
        "ingest": res.grade == "business",
        "audit": res.audit,
    }


def should_ingest(diff_payload):
    """只有业务级（business）变更才入库，其余只记审计。"""
    return bool(diff_payload.get("ingest"))
