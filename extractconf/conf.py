"""配置驱动的字段抽取加载器。

配置为 JSON，顶层包含 "fields"：

    {
      "fields": {
        "title": {
          "rules": [
            {"name": "css-title", "type": "css", "value": "h1.title"},
            {"name": "re-title", "type": "regex", "value": "标题：(\\S+)"},
            {"name": "json-title", "type": "jsonpath", "value": "$.article.title"}
          ],
          "type": "str",
          "default": "",
          "required": false
        }
      }
    }

规则按顺序回退，第一个命中的规则生效。旧的 {"字段": "正则"} 纯正则
映射仍然兼容。错误码见 README。
"""
import json
import re
from html.parser import HTMLParser

RULE_TYPES = ("css", "regex", "jsonpath")


class ConfigError(ValueError):
    """配置结构错误，消息格式：错误码 文件 字段: 原因。"""


class ExtractError(ValueError):
    """必填字段抽取失败或类型转换失败。"""


class Result(dict):
    """抽取结果，matched 记录每个字段命中的规则名。"""

    def __init__(self, values, matched):
        super().__init__(values)
        self.matched = matched


def _to_bool(value):
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    raise ValueError("无法转换为 bool: %r" % value)


_CONVERTERS = {
    "str": str,
    "int": lambda v: int(str(v).strip()),
    "float": lambda v: float(str(v).strip()),
    "bool": _to_bool,
}

_MISSING = object()


# ---------------------------------------------------------------- 配置加载

def load(path):
    """加载并校验配置文件，结构错误抛 ConfigError（带文件、字段、原因）。"""
    try:
        with open(path, encoding="utf-8") as handle:
            conf = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError("E001 %s: JSON 解析失败: %s" % (path, exc)) from exc
    if isinstance(conf, dict) and "fields" in conf:
        _validate(conf, path)
    return conf


def _fail(code, path, field, reason):
    where = " 字段 %s" % field if field else ""
    raise ConfigError("%s %s%s: %s" % (code, path, where, reason))


def _validate(conf, path):
    fields = conf["fields"]
    if not isinstance(fields, dict) or not fields:
        _fail("E002", path, None, '"fields" 必须是非空对象')
    for field, spec in fields.items():
        if not isinstance(spec, dict):
            _fail("E003", path, field, "字段定义必须是对象")
        rules = spec.get("rules")
        if not isinstance(rules, list) or not rules:
            _fail("E004", path, field, '"rules" 必须是非空列表')
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("name"), str):
                _fail("E005", path, field, '规则缺少字符串 "name"')
            if rule.get("type") not in RULE_TYPES:
                _fail("E005", path, field,
                      "规则 %s 的 type 未知: %r（支持 %s）"
                      % (rule["name"], rule.get("type"), "/".join(RULE_TYPES)))
            if not isinstance(rule.get("value"), str):
                _fail("E005", path, field, "规则 %s 缺少字符串 value" % rule["name"])
            if rule["type"] == "regex":
                try:
                    re.compile(rule["value"])
                except re.error as exc:
                    _fail("E006", path, field,
                          "规则 %s 正则编译失败: %s" % (rule["name"], exc))
            if rule["type"] == "jsonpath" and not _PATH_RE.fullmatch(rule["value"]):
                _fail("E005", path, field,
                      "规则 %s 的 JSON 路径非法: %r" % (rule["name"], rule["value"]))
        conv = spec.get("type", "str")
        if conv not in _CONVERTERS:
            _fail("E007", path, field, "未知类型转换: %r（支持 %s）"
                  % (conv, "/".join(_CONVERTERS)))


# ---------------------------------------------------------------- 抽取入口

def apply(page, conf):
    """按配置抽取字段，返回 Result（含 matched 规则名）。"""
    if not (isinstance(conf, dict) and "fields" in conf):
        return _apply_legacy(page, conf)
    ctx = _Context(page)
    values, matched = {}, {}
    for field, spec in conf["fields"].items():
        raw, rule_name = _run_rules(ctx, spec["rules"])
        if raw is _MISSING:
            if "default" in spec:
                values[field], matched[field] = spec["default"], None
                continue
            if spec.get("required"):
                raise ExtractError("E008 字段 %s: 必填但所有规则均未命中" % field)
            values[field], matched[field] = "", None
            continue
        conv = spec.get("type", "str")
        try:
            values[field] = _CONVERTERS[conv](raw)
        except (ValueError, TypeError) as exc:
            if "default" in spec:
                values[field], matched[field] = spec["default"], None
                continue
            if spec.get("required"):
                raise ExtractError(
                    "E009 字段 %s: 类型转换 %s 失败: %s" % (field, conv, exc))
            values[field], matched[field] = "", None
            continue
        matched[field] = rule_name
    return Result(values, matched)


def _apply_legacy(page, conf):
    values, matched = {}, {}
    for field, pattern in conf.items():
        match = re.search(pattern, page)
        values[field] = match.group(1) if match else ""
        matched[field] = "regex" if match else None
    return Result(values, matched)


def _run_rules(ctx, rules):
    for rule in rules:
        if rule["type"] == "css":
            hit = ctx.select(rule["value"])
        elif rule["type"] == "regex":
            hit = _regex(ctx.page, rule["value"])
        else:
            hit = ctx.json_path(rule["value"])
        if hit is not None:
            return hit, rule["name"]
    return _MISSING, None


def _regex(page, pattern):
    match = re.search(pattern, page)
    if not match:
        return None
    return match.group(1) if match.groups() else match.group(0)


class _Context:
    """按需解析页面：CSS 规则触发 HTML 解析，JSON 路径触发 JSON 解析。"""

    def __init__(self, page):
        self.page = page
        self._tree = None
        self._json = _MISSING

    def select(self, selector):
        if self._tree is None:
            self._tree = _parse_html(self.page if isinstance(self.page, str) else "")
        nodes = _select(self._tree, selector)
        return nodes[0].text() if nodes else None

    def json_path(self, path):
        if self._json is _MISSING:
            try:
                self._json = (json.loads(self.page)
                              if isinstance(self.page, str) else self.page)
            except (json.JSONDecodeError, TypeError):
                self._json = None
        if self._json is None:
            return None
        try:
            return _json_path(self._json, path)
        except (KeyError, IndexError, TypeError):
            return None


# ---------------------------------------------------------------- JSON 路径

_PATH_RE = re.compile(r"\$(?:\.[A-Za-z_][\w-]*|\[\d+\])*")
_PATH_TOKEN_RE = re.compile(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]")


def _json_path(data, path):
    current = data
    for match in _PATH_TOKEN_RE.finditer(path, 1):
        key, index = match.groups()
        current = current[int(index)] if index is not None else current[key]
    return current


# ---------------------------------------------------------------- CSS 选择器

class _Node:
    __slots__ = ("tag", "attrs", "parent", "content")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.parent = None
        self.content = []

    def text(self):
        parts = []
        for item in self.content:
            parts.append(item if isinstance(item, str) else item.text())
        return "".join(parts).strip()


class _TreeBuilder(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack = [self.root]

    def _add(self, tag, attrs):
        node = _Node(tag.lower(), dict(attrs))
        node.parent = self.stack[-1]
        self.stack[-1].content.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._add(tag, attrs)
        if tag.lower() not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag.lower():
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].content.append(data)


def _parse_html(page):
    builder = _TreeBuilder()
    builder.feed(page)
    builder.close()
    return builder.root


_SIMPLE_RE = re.compile(r"[\w-]+|[#.][\w-]+|\[[^\]]*\]")
_ATTR_RE = re.compile(r"\[([\w-]+)(?:=[\"']?([^\"'\]]*)[\"']?)?\]")


def _parse_simple(token):
    tag, ident, classes, attrs = "*", None, [], []
    for part in _SIMPLE_RE.findall(token):
        if part.startswith("#"):
            ident = part[1:]
        elif part.startswith("."):
            classes.append(part[1:])
        elif part.startswith("["):
            match = _ATTR_RE.fullmatch(part)
            if match:
                attrs.append((match.group(1), match.group(2)))
        else:
            tag = part.lower()
    return tag, ident, classes, attrs


def _matches(node, simple):
    tag, ident, classes, attrs = simple
    if tag != "*" and node.tag != tag:
        return False
    if ident is not None and node.attrs.get("id") != ident:
        return False
    node_classes = node.attrs.get("class", "").split()
    if any(cls not in node_classes for cls in classes):
        return False
    for name, value in attrs:
        if name not in node.attrs or (value is not None and node.attrs[name] != value):
            return False
    return True


def _walk(node):
    for item in node.content:
        if isinstance(item, _Node):
            yield item
            yield from _walk(item)


def _match_chain(node, steps, index):
    if not _matches(node, steps[index][1]):
        return False
    if index == 0:
        return True
    parent = node.parent
    if steps[index][0] == ">":
        return (parent is not None and parent.tag != "#root"
                and _match_chain(parent, steps, index - 1))
    ancestor = parent
    while ancestor is not None and ancestor.tag != "#root":
        if _match_chain(ancestor, steps, index - 1):
            return True
        ancestor = ancestor.parent
    return False


def _select(root, selector):
    steps = []
    combinator = None
    for part in selector.replace(">", " > ").split():
        if part == ">":
            combinator = ">"
        else:
            steps.append((combinator, _parse_simple(part)))
            combinator = " "
    if not steps:
        return []
    return [node for node in _walk(root)
            if _match_chain(node, steps, len(steps) - 1)]

