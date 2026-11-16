"""按回显位置判断的反射型 / 存储型 XSS 检测引擎。

旧实现只做原文匹配：编码过的回显算没命中，解码后的回显又被当成命中，
同一页面给出两种结论。本模块改为：

1. 用 payload 的字母数字骨架定位所有反射点（原样、HTML 实体编码、
   URL 百分号编码、双重编码都能找到），逐处判断、不因一处命中跳过其余；
2. 按回显所在上下文（HTML 正文 / 属性 / 脚本 / URL）判断编码后能否执行；
3. 编码过的内容先还原再判断，完整还原过程随结论一起返回；
4. 同一页面、同一上下文、同一种还原方式的重复回显合并成一条；
5. 拿不准的（残缺结构、部分编码、多重编码等）标记 review 留待人工确认；
6. 检测结束给出未覆盖参数及跳过原因，页面结构变化导致的解析失败单列。

纯 Python 标准库实现，线程安全。
"""

import html
import re
import threading
import urllib.parse

__all__ = [
    "PAYLOAD",
    "CONTEXT_HTML",
    "CONTEXT_ATTR",
    "CONTEXT_SCRIPT",
    "CONTEXT_URL",
    "VERDICT_HIT",
    "VERDICT_SAFE",
    "VERDICT_REVIEW",
    "ReflectEngine",
    "load",
    "unload",
    "add_entry",
    "remove_entry",
    "check",
    "check_all",
    "report",
    "reset",
]

PAYLOAD = "<script>alert(1)</script>"

CONTEXT_HTML = "html"        # HTML 正文（标签之间的文本节点）
CONTEXT_ATTR = "attribute"   # 标签属性值（含事件处理属性）
CONTEXT_SCRIPT = "script"    # <script> 元素内部
CONTEXT_URL = "url"          # href / src / action 等 URL 型属性

VERDICT_HIT = "hit"          # 该上下文下编码拦不住，可执行
VERDICT_SAFE = "safe"        # 回显存在但编码有效，无法执行
VERDICT_REVIEW = "review"    # 拿不准，留待人工确认

# 检测时最多接受的还原轮数；超过仍未还原成 payload 说明是多重/混合编码。
_MAX_RESTORE_ROUNDS = 6

# 带 URL 语义的属性：值里出现 javascript: 且未被编码即可执行。
_URL_ATTRS = frozenset(("href", "src", "action", "formaction", "data",
                        "poster", "background", "cite", "longdesc", "xlink:href"))

_JS_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}")
_TAG_NAME_RE = re.compile(r"[A-Za-z][-A-Za-z0-9_:]*")


# ---------------------------------------------------------------------------
# 编码 / 还原
# ---------------------------------------------------------------------------

def _decode_html(text):
    """HTML 实体解码；没有实体时返回 None。"""
    if "&" not in text:
        return None
    out = html.unescape(text)
    return out if out != text else None


def _decode_url(text):
    """URL 百分号解码；没有百分号序列时返回 None。"""
    if "%" not in text:
        return None
    out = urllib.parse.unquote(text)
    return out if out != text else None


def _decode_js_escape(text):
    """脚本上下文里的 JS 字符串转义还原；没有转义时返回 None。

    覆盖 \\uXXXX、\\xXX 以及对引号/斜杠/尖括号等标点的反斜杠转义
    （如 \\/、\\'、\\"），这些在 JS 字符串里会被还原成原字符。
    """
    if not (_JS_ESCAPE_RE.search(text) or "\\" in text):
        return None

    def _hex_one(match):
        return chr(int(match.group(0)[2:], 16))

    out = _JS_ESCAPE_RE.sub(_hex_one, text)
    if "\\" in out:
        # 去掉对非特殊标点的反斜杠前缀（JS 字符串里 \/ \' \" 等价原字符）。
        out = re.sub(r"\\([\\/'\"`<>()])", r"\1", out)
    return out if out != text else None


def _restore(raw, context, payload):
    """把回显原文逐步还原成 payload，返回 (还原结果, 步骤列表)。

    每一步记录编码类型、还原前片段和还原后片段。脚本上下文额外尝试
    JS 转义还原。还原不出 payload 时结果为 None，由调用方按 review 处理。
    """
    steps = []
    current = raw
    for _ in range(_MAX_RESTORE_ROUNDS):
        if current == payload:
            return current, steps
        candidates = [
            ("url", _decode_url(current)),
            ("html", _decode_html(current)),
        ]
        if context == CONTEXT_SCRIPT:
            candidates.append(("js_escape", _decode_js_escape(current)))
        progressed = False
        for kind, decoded in candidates:
            if decoded is not None and decoded != current:
                steps.append({
                    "encoding": kind,
                    "from": current,
                    "to": decoded,
                })
                current = decoded
                progressed = True
                break
        if not progressed:
            break
    return (current if current == PAYLOAD else None), steps


# ---------------------------------------------------------------------------
# 反射点定位
# ---------------------------------------------------------------------------

# 每个 payload 特殊字符对应的“可能以任何编码形式出现”的匹配组，
# 覆盖单重、双重和三重百分号编码（三重编码还原轮数多，结论留待人工确认）。
_CHAR_CLASSES = {
    "<": r"(?:<|&lt;|&#60;|&#x3c;|%3[cC]|%253[cC]|%25253[cC])",
    ">": r"(?:>|&gt;|&#62;|&#x3e;|%3[eE]|%253[eE]|%25253[eE])",
    "/": r"(?:/|&#47;|&#x2f;|%2[fF]|%252[fF]|%25252[fF]|\\/)",
    "(": r"(?:\(|&#40;|&#x28;|%28|%2528|%252528)",
    ")": r"(?:\)|&#41;|&#x29;|%29|%2529|%252529)",
    '"': r'(?:"|&quot;|&#34;|&#x22;|%22|%2522|%252522|\\")',
    "'": r"(?:'|&#39;|&#x27;|%27|%2527|%252527|\\')",
    ":": r"(?::|&#58;|&#x3a;|%3[aA]|%253[aA]|%25253[aA])",
    "=": r"(?:=|&#61;|&#x3d;|%3[dD]|%253[dD]|%25253[dD])",
    ";": r"(?:;|&#59;|&#x3b;|%3[bB]|%253[bB]|%25253[bB])",
    " ": r"(?: |&#32;|%20|%2520|%252520|\+)",
}

# 脚本上下文额外允许的 JS 字符串转义形态。
_SCRIPT_EXTRA_CLASSES = {
    "<": r"|\\u003[cC]|\\x3[cC]",
    ">": r"|\\u003[eE]|\\x3[eE]",
    "/": r"|\\u002[fF]|\\x2[fF]",
    "(": r"|\\u0028|\\x28",
    ")": r"|\\u0029|\\x29",
}


def _build_reflection_regex(payload, context=None):
    """构造反射点匹配正则：字母数字按原样，特殊字符按编码类匹配。

    context 为 script 时额外接受 JS 字符串转义（\\/、\\'、\\uXXXX 等），
    因为 <script> 内的回显常以 JS 转义形态出现。
    """
    parts = []
    for ch in payload:
        if ch.isalnum():
            parts.append(re.escape(ch))
            continue
        pattern = _CHAR_CLASSES.get(ch)
        if pattern is None:
            # 未预置的特殊字符：只认原样与百分号编码两种形态。
            pattern = r"(?:%s|%%%02X|%%%02x)" % (re.escape(ch), ord(ch), ord(ch))
        if context == CONTEXT_SCRIPT and ch in _SCRIPT_EXTRA_CLASSES:
            pattern = pattern[:-1] + _SCRIPT_EXTRA_CLASSES[ch] + r")"
        parts.append(pattern)
    return re.compile("".join(parts), re.IGNORECASE)


def _find_reflections(body, payload, context=None):
    """返回所有反射点 (起始偏移, 结束偏移, 回显原文)，允许重叠。"""
    if not payload:
        return []
    regex = _build_reflection_regex(payload, context)
    reflections = []
    for match in regex.finditer(body):
        reflections.append((match.start(), match.end(), match.group(0)))
    return reflections


# ---------------------------------------------------------------------------
# 页面结构扫描：把正文切成带上下文的区域
# ---------------------------------------------------------------------------

def _scan_regions(body):
    """把页面正文切成区域列表，每项为 (start, end, kind, meta)。

    kind 取值：text（HTML 正文）、attr（属性值）、script（脚本内部）、
    comment（注释，回显在其中不可执行但单独标注）、doctype。
    attr 区域的 meta 为 (属性名, 引号字符或 None)。
    结构残缺（引号不闭合等）的位置通过第二个返回值给出。
    """
    regions = []
    parse_errors = []
    n = len(body)
    i = 0

    while i < n:
        ch = body[i]
        if ch != "<":
            start = i
            j = body.find("<", i + 1)
            if j < 0:
                j = n
            regions.append((start, j, "text", None))
            i = j
            continue

        # 注释 <!-- ... -->
        if body.startswith("<!--", i):
            end = body.find("-->", i + 4)
            if end < 0:
                parse_errors.append({
                    "offset": i,
                    "reason": "HTML 注释未闭合（找不到 -->）",
                })
                end = n
            else:
                end += 3
            regions.append((i, end, "comment", None))
            i = end
            continue

        # DOCTYPE / 声明
        if body.startswith("<!", i) or body.startswith("<?", i):
            end = body.find(">", i)
            if end < 0:
                parse_errors.append({
                    "offset": i,
                    "reason": "声明节点未闭合（找不到 >）",
                })
                end = n
            else:
                end += 1
            regions.append((i, end, "doctype", None))
            i = end
            continue

        # 普通标签：找标签名与结束的 >
        name_match = _TAG_NAME_RE.match(body, i + 1)
        tag_start = i
        if name_match is None:
            # 孤立的 "<"，当文本处理。
            regions.append((i, i + 1, "text", None))
            i += 1
            continue
        tag_name = name_match.group(0).lower()
        k = name_match.end()

        if tag_name == "script":
            # <script> 整段（含标签头）先登记属性区域，再定位脚本体。
            tag_end = _find_tag_end(body, k)
            if tag_end < 0:
                parse_errors.append({
                    "offset": tag_start,
                    "reason": "<script> 标签未闭合（找不到 >）",
                })
                regions.append((tag_start, n, "text", None))
                break
            # 标签头里的属性照常解析。
            _scan_tag_attributes(body, tag_start, tag_end + 1, k,
                                 regions, parse_errors)
            close = re.compile(r"</script\s*>", re.IGNORECASE)
            close_match = close.search(body, tag_end + 1)
            if close_match is None:
                parse_errors.append({
                    "offset": tag_start,
                    "reason": "<script> 缺少对应的 </script> 结束标签",
                })
                regions.append((tag_end + 1, n, "script", None))
                break
            # 脚本体（含其中出现的字面量 </script>，HTML 里它本就会闭合
            # 脚本块，因此落在脚本区的未编码回显直接判 hit）。
            regions.append((tag_end + 1, close_match.end(), "script", None))
            i = close_match.end()
            continue

        tag_end = _find_tag_end(body, k)
        if tag_end < 0:
            parse_errors.append({
                "offset": tag_start,
                "reason": "标签 <%s> 未闭合（找不到 >）" % tag_name,
            })
            regions.append((tag_start, n, "text", None))
            break
        if body.startswith("/", i + 1):
            # 结束标签整体按文本区域处理。
            regions.append((tag_start, tag_end + 1, "text", None))
            i = tag_end + 1
            continue
        _scan_tag_attributes(body, tag_start, tag_end + 1, k,
                             regions, parse_errors)
        i = tag_end + 1

    return regions, parse_errors


def _find_tag_end(body, start):
    """返回标签结束 '>' 的偏移；引号内的 '>' 不算标签结束。找不到返回 -1。"""
    quote = None
    i = start
    n = len(body)
    while i < n:
        ch = body[i]
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ">":
            return i
        i += 1
    return -1


def _deepest_region(offset, end, regions):
    """返回完全覆盖该 span 的最小区域 (kind, meta)；没有则按 HTML 正文。"""
    best = None
    for start, region_end, kind, meta in regions:
        if start <= offset and end <= region_end:
            if best is None or (region_end - start) < (best[1] - best[0]):
                best = (start, region_end, kind, meta)
    if best is None:
        return "text", None
    return best[2], best[3]


def _region_at_offset(offset, regions):
    """返回包含该偏移的最小区域 (kind, meta, start, end)；否则 None。"""
    best = None
    for start, region_end, kind, meta in regions:
        if start <= offset < region_end:
            if best is None or (region_end - start) < (best[3] - best[2]):
                best = (kind, meta, start, region_end)
    return best


def _scan_tag_attributes(body, tag_start, tag_end, cursor,
                         regions, parse_errors):
    """扫描一个开始标签内部，把属性值切成 attr 区域，其余按 text 登记。"""
    # 标签名到第一个属性之间的头部。
    if cursor < tag_end:
        regions.append((tag_start, cursor, "text", None))
    i = cursor
    seg_start = cursor
    while i < tag_end:
        ch = body[i]
        if ch.isspace():
            i += 1
            continue
        name_match = _TAG_NAME_RE.match(body, i)
        if name_match is None:
            i += 1
            continue
        attr_name = name_match.group(0).lower()
        j = name_match.end()
        while j < tag_end and body[j].isspace():
            j += 1
        if j < tag_end and body[j] == "=":
            j += 1
            while j < tag_end and body[j].isspace():
                j += 1
            if seg_start < i:
                regions.append((seg_start, i, "text", None))
            if j < tag_end and body[j] in ("'", '"'):
                quote = body[j]
                value_start = j + 1
                value_end = body.find(quote, value_start, tag_end)
                if value_end < 0:
                    parse_errors.append({
                        "offset": j,
                        "reason": "属性 %s 的引号 %s 未在标签内闭合"
                                  % (attr_name, quote),
                    })
                    value_end = tag_end - 1
                    regions.append((j, tag_end, "attr",
                                    (attr_name, quote)))
                    seg_start = tag_end
                    i = tag_end
                    continue
                regions.append((j, value_end + 1, "attr",
                                (attr_name, quote)))
                seg_start = value_end + 1
                i = value_end + 1
                continue
            # 无引号属性值：读到空白或标签结束。
            value_start = j
            while j < tag_end and not body[j].isspace():
                j += 1
            regions.append((value_start, j, "attr", (attr_name, None)))
            seg_start = j
            i = j
            continue
        # 布尔属性（没有 =）。
        i = name_match.end()
    if seg_start < tag_end:
        regions.append((seg_start, tag_end, "text", None))


# ---------------------------------------------------------------------------
# 按上下文判断能否执行
# ---------------------------------------------------------------------------

def _has_raw(text, chars):
    """回显原文里是否出现未编码的指定字符。"""
    return any(ch in text for ch in chars)


def _verdict_html(raw, restored):
    """HTML 正文：尖括号未编码即可注入标签；实体/百分号编码均安全。"""
    if restored is None:
        return VERDICT_REVIEW, "多重或混合编码，无法确认还原结果，需人工确认"
    if _has_raw(raw, "<>"):
        return VERDICT_HIT, "HTML 正文中尖括号未编码，可注入新标签执行脚本"
    return VERDICT_SAFE, "尖括号被实体或百分号编码，浏览器不会解析为标签"


def _verdict_attribute(raw, restored, attr_name, quote):
    """属性值：能跳出属性或注入事件处理属性才命中。

    URL 型属性在 _context_of 阶段已归入 CONTEXT_URL，不会走到这里。
    """
    if restored is None:
        return VERDICT_REVIEW, "多重或混合编码，无法确认还原结果，需人工确认"
    if attr_name.startswith("on"):
        # 事件处理属性按 JS 执行，括号/引号未编码即可闭合表达式。
        if _has_raw(raw, "()\"'"):
            return VERDICT_HIT, "事件处理属性 %s 中 JS 语法字符未编码，可注入脚本" % attr_name
        return VERDICT_SAFE, "事件处理属性 %s 中危险字符已编码" % attr_name
    if quote is None:
        # 无引号属性：空白即可断开并追加新属性。
        if any(ch in raw for ch in " \t\n\r"):
            return VERDICT_HIT, "无引号属性中出现空白，可追加事件处理属性"
        return VERDICT_SAFE, "无引号属性但回显不含空白，无法断开属性"
    if _has_raw(raw, quote):
        return VERDICT_HIT, "属性定界引号 %s 未编码，可闭合属性后注入事件处理属性" % quote
    return VERDICT_SAFE, "属性定界引号 %s 已编码，无法跳出属性" % quote


def _verdict_script(raw, restored, body, offset):
    """<script> 内部：闭合脚本标签或断开 JS 字符串/语句才命中。"""
    lowered = raw.lower()
    # 未编码的 </script> 直接闭合脚本块，最优先。
    if "</script" in lowered:
        return VERDICT_HIT, "回显含未编码的 </script>，可闭合脚本块"
    quote = _enclosing_script_string(body, offset)
    # 回显里出现与外层字符串相同的未转义定界符，可闭合字符串注入语句。
    if quote is not None and re.search(r"(?<!\\)" + re.escape(quote), raw):
        return VERDICT_HIT, ("脚本字符串定界符 %s 未转义，可闭合字符串注入语句"
                             % quote)
    if restored is not None:
        # 能完整还原成 payload，且闭合脚本块所需的 </script 已被转义
        # （开头单独的 < 不构成闭合），说明只是字符串数据。
        if re.search(r"(?<!\\)</script", lowered):
            return VERDICT_HIT, "回显含未转义的 </script>，可闭合脚本块"
        return VERDICT_SAFE, "脚本内回显为字符串数据，脚本闭合序列与定界符均已转义"
    # 还原失败且没有明确闭合手段：多重/混合编码，交人工确认。
    return VERDICT_REVIEW, "脚本上下文编码复杂或结构不明确，需人工确认"


def _enclosing_script_string(body, offset):
    """判断 offset 是否处于 <script> 内的 JS 字符串中，返回定界引号。"""
    i = 0
    quote = None
    while i < offset:
        ch = body[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        i += 1
    return quote


def _verdict_url(raw, restored):
    """URL 型属性：未编码的可执行方案命中，其余安全/待确认。

    入参 raw 是整个属性值（已去定界引号），不只反射片段，
    因此能看到 javascript: / data: 等协议前缀。
    """
    if restored is None:
        return VERDICT_REVIEW, "URL 多重或混合编码，方案是否可执行需人工确认"
    lead = raw.strip().lower()
    # 未编码的可执行方案（冒号是字面量，浏览器才会识别为协议切换）。
    if lead.startswith(("javascript:", "vbscript:")) \
            or ";javascript:" in lead:
        return VERDICT_HIT, "URL 中可执行脚本方案未编码，可直接执行脚本"
    if lead.startswith("data:text/html"):
        return VERDICT_HIT, "URL 中 data:text/html 方案可携带脚本执行"
    # 相对路径、锚点等不含协议前缀，天然无法切换到脚本方案。
    scheme_match = re.match(r"^([a-z][a-z0-9+.-]*):", lead)
    if scheme_match is None:
        return VERDICT_SAFE, "URL 为相对路径或锚点，不携带可执行脚本"
    # 走到这里说明含字面量冒号；若方案不是已知安全方案，再判断编码情况。
    scheme = scheme_match.group(1)
    if scheme in ("http", "https", "ftp", "mailto", "tel", "file"):
        return VERDICT_SAFE, "URL 为普通导航方案 %s，不携带可执行脚本" % scheme
    # 冒号被实体/百分号编码时 scheme_match 已匹配不到，能匹配说明冒号原样；
    # 其余未知方案是否可执行交给人工确认。
    return VERDICT_REVIEW, "URL 方案 %r 能否执行需人工确认" % scheme


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class ReflectEngine:
    """页面注册表 + 反射检测引擎。线程安全，可通过 reset() 清空重建。"""

    def __init__(self, default_payload=PAYLOAD):
        self._lock = threading.RLock()
        self._pages = {}          # page -> body
        self._entries = {}        # page -> [{"param", "stored", "reason"}]
        self._last_findings = []  # 最近一次 check / check_all 的合并结论
        self._last_uncovered = []
        self._last_parse_errors = {}
        self._default_payload = default_payload

    # -- 注册 ----------------------------------------------------------

    def load(self, page, body):
        """登记页面正文，返回页面名与正文长度。"""
        if not isinstance(page, str) or not page:
            raise ValueError("page 不能为空")
        if not isinstance(body, str):
            raise ValueError("body 必须是字符串")
        with self._lock:
            self._pages[page] = body
        return {"page": page, "size": len(body)}

    def unload(self, page):
        """移除已登记页面及其入口，返回是否实际移除。"""
        with self._lock:
            existed = self._pages.pop(page, None) is not None
            self._entries.pop(page, None)
            return existed

    def add_entry(self, page, param, stored=False, reason=""):
        """登记检测入口。

        反射型入口对应一个会原样反射参数的页面；存储型入口没有可直接
        探测的请求参数，先登记、再由 check(page, param) 对页面正文复检。
        reason 用于登记入口暂时跳过的原因（如需要登录态）。
        """
        if not isinstance(page, str) or not page:
            raise ValueError("page 不能为空")
        if not isinstance(param, str) or not param:
            raise ValueError("参数名不能为空")
        with self._lock:
            entries = self._entries.setdefault(page, [])
            for item in entries:
                if item["param"] == param:
                    item.update({"stored": bool(stored), "reason": reason})
                    return {"page": page, "param": param, "updated": True}
            entries.append({"param": param, "stored": bool(stored),
                            "reason": reason})
            return {"page": page, "param": param, "updated": False}

    def remove_entry(self, page, param):
        """移除检测入口，返回是否实际移除。"""
        with self._lock:
            entries = self._entries.get(page)
            if not entries:
                return False
            before = len(entries)
            self._entries[page] = [item for item in entries
                                   if item["param"] != param]
            return len(self._entries[page]) != before

    # -- 检测 ----------------------------------------------------------

    def check(self, page, param, payload=None):
        """检测单个参数在页面中的全部反射点。

        返回命中位置、上下文、payload、结论与完整还原过程；同一参数在
        多个位置出现时逐处判断，重复回显按上下文+还原方式合并成一条。
        """
        if not isinstance(param, str) or not param:
            raise ValueError("参数名不能为空")
        with self._lock:
            body = self._pages.get(page)
            if body is None:
                raise ValueError("页面未载入: %r" % page)
            probe = payload or self._default_payload
            if not isinstance(probe, str) or not probe:
                raise ValueError("payload 必须是非空字符串")

            regions, parse_errors = _scan_regions(body)
            self._last_parse_errors = {page: [dict(item) for item
                                              in parse_errors]}
            # 按区域逐处定位：脚本区域用带 JS 转义的匹配正则，
            # 其余区域用通用（HTML 实体 / 百分号编码）正则。
            raw_reflections = []
            seen = set()
            for start, end, kind, meta in regions:
                if kind in ("doctype",):
                    continue
                context_for_regex = CONTEXT_SCRIPT if kind == "script" else None
                for offset, rel_end, raw in _find_reflections(
                        body[start:end], probe, context_for_regex):
                    span = (start + offset, start + rel_end)
                    if span in seen:
                        continue
                    seen.add(span)
                    raw_reflections.append((span[0], span[1], raw, kind, meta))

            # 兜底：整页再扫一遍（脚本与通用两种正则都用），把区域扫描
            # 因结构残缺漏掉的反射点补回来，按最深覆盖区域归类。
            for context_for_regex in (None, CONTEXT_SCRIPT):
                for offset, rel_end, raw in _find_reflections(
                        body, probe, context_for_regex):
                    span = (offset, rel_end)
                    if span in seen:
                        continue
                    seen.add(span)
                    kind, meta = _deepest_region(span[0], span[1], regions)
                    host = _region_at_offset(span[0], regions)
                    # 反射横跨无引号属性边界（回显里的空格把属性断开，
                    # 后面的内容被解析成新属性）：按起始处的属性归类。
                    if kind == "text" and host is not None \
                            and host[0] == "attr":
                        host_quote = host[1][1]
                        if host_quote is None \
                                and any(c in raw for c in " \t\n\r"):
                            kind, meta = host[0], host[1]
                        # 引号属性：回显含未编码的定界引号即可闭合属性。
                        elif host_quote is not None and host_quote in raw:
                            kind, meta = host[0], host[1]
                    raw_reflections.append(
                        (span[0], span[1], raw, kind, meta))

            merged = {}
            order = []
            for offset, end, raw, kind, meta in raw_reflections:
                context, attr_name, quote = self._context_of(kind, meta)
                restored, restore_steps = _restore(raw, context, probe)
                if kind == "comment":
                    verdict = VERDICT_SAFE
                    reason = "回显位于 HTML 注释中，注释内容不参与页面解析"
                else:
                    # URL 型属性要结合整个属性值判断协议方案，
                    # 反射片段本身可能不含协议前缀。
                    full_value = None
                    if context == CONTEXT_URL:
                        host = _region_at_offset(offset, regions)
                        if host is not None and host[0] == "attr":
                            rs, re_ = host[2], host[3]
                            full_value = body[rs:re_].strip("\"' \t\n\r")
                    verdict, reason = self._judge(
                        context, raw, restored, body, offset,
                        attr_name, quote, full_value)
                key = (context, attr_name or "",
                       tuple((step["encoding"], step["from"], step["to"])
                             for step in restore_steps),
                       verdict)
                if key in merged:
                    merged[key]["offsets"].append(offset)
                    merged[key]["occurrences"] += 1
                    continue
                finding = {
                    "page": page,
                    "param": param,
                    "payload": probe,
                    "context": context,
                    "attribute": attr_name,
                    "in_comment": kind == "comment",
                    "offset": offset,
                    "offsets": [offset],
                    "occurrences": 1,
                    "raw": raw,
                    "decoded": restored or "",
                    "restore": [dict(step) for step in restore_steps],
                    "verdict": verdict,
                    "reason": reason,
                }
                merged[key] = finding
                order.append(key)

            findings = [merged[key] for key in order]
            uncovered = self._uncovered_for(page, param)
            self._last_findings = [dict(item) for item in findings]
            self._last_uncovered = [dict(item) for item in uncovered]
            return {
                "page": page,
                "param": param,
                "findings": [dict(item) for item in findings],
                "uncovered": [dict(item) for item in uncovered],
                "parse_errors": [dict(item) for item in parse_errors],
            }

    def check_all(self, payload=None):
        """对所有已登记入口逐页逐参数检测（存储型入口同样覆盖）。

        只处理登记过入口的页面；已载入但没有入口的页面不在检测范围，
        不计入 parse_errors。每个入口的跳过原因汇入 uncovered。
        """
        with self._lock:
            pages = sorted(self._entries)
            all_findings = []
            all_uncovered = []
            all_parse_errors = {}
            for page in pages:
                if page not in self._pages:
                    # 该页所有入口都无法检测，逐个登记未覆盖原因。
                    for item in self._entries[page]:
                        all_uncovered.append({
                            "page": page,
                            "param": item["param"],
                            "stored": item["stored"],
                            "reason": item.get("reason")
                                    or "页面正文未载入，无法检测",
                        })
                    continue
                params = sorted({item["param"]
                                 for item in self._entries[page]})
                for param in params:
                    result = self.check(page, param, payload=payload)
                    all_findings.extend(result["findings"])
                    all_uncovered.extend(result["uncovered"])
                    if result["parse_errors"]:
                        all_parse_errors[page] = result["parse_errors"]
            self._last_findings = [dict(item) for item in all_findings]
            self._last_uncovered = [dict(item) for item in all_uncovered]
            self._last_parse_errors = {
                key: [dict(item) for item in value]
                for key, value in all_parse_errors.items()
            }
            return {
                "findings": [dict(item) for item in all_findings],
                "uncovered": [dict(item) for item in all_uncovered],
                "parse_errors": {
                    key: [dict(item) for item in value]
                    for key, value in all_parse_errors.items()
                },
            }

    def report(self):
        """按页面汇总检测结论，供运营按页面查看。"""
        with self._lock:
            pages = {}
            contexts = {}
            verdicts = {VERDICT_HIT: 0, VERDICT_SAFE: 0, VERDICT_REVIEW: 0}
            for item in self._last_findings:
                page = item.get("page", "_")
                pages[page] = pages.get(page, 0) + 1
                contexts[item["context"]] = contexts.get(item["context"], 0) + 1
                verdicts[item["verdict"]] = verdicts.get(item["verdict"], 0) + 1
            parse_error_pages = {
                page: len(errors)
                for page, errors in self._last_parse_errors.items() if errors
            }
            return {
                "hits": verdicts[VERDICT_HIT],
                "review": verdicts[VERDICT_REVIEW],
                "safe": verdicts[VERDICT_SAFE],
                "findings": len(self._last_findings),
                "pages": pages,
                "params": self._param_counts(),
                "contexts": contexts,
                "verdicts": verdicts,
                "uncovered": len(self._last_uncovered),
                "uncovered_items": [dict(item)
                                    for item in self._last_uncovered],
                "parse_errors": sum(parse_error_pages.values()),
                "parse_error_pages": parse_error_pages,
            }

    def reset(self):
        """清空全部页面、入口与最近一次检测结果。"""
        with self._lock:
            self._pages.clear()
            self._entries.clear()
            self._last_findings = []
            self._last_uncovered = []
            self._last_parse_errors = {}

    # -- 内部 ----------------------------------------------------------

    @staticmethod
    def _context_of(kind, meta):
        if kind == "attr":
            attr_name, quote = meta if meta else (None, None)
            if attr_name in _URL_ATTRS:
                return CONTEXT_URL, attr_name, quote
            return CONTEXT_ATTR, attr_name, quote
        if kind == "script":
            return CONTEXT_SCRIPT, None, None
        if kind == "comment":
            # 注释内容不参与 HTML 解析，按 HTML 正文口径但结构上不可执行，
            # 交给 verdict 阶段按安全处理；这里仍标注真实所在区域。
            return CONTEXT_HTML, None, None
        return CONTEXT_HTML, None, None

    @staticmethod
    def _judge(context, raw, restored, body, offset, attr_name, quote,
               full_value=None):
        if context == CONTEXT_HTML:
            verdict, reason = _verdict_html(raw, restored)
        elif context == CONTEXT_ATTR:
            verdict, reason = _verdict_attribute(
                raw, restored, attr_name or "", quote)
        elif context == CONTEXT_SCRIPT:
            verdict, reason = _verdict_script(raw, restored, body, offset)
        else:
            verdict, reason = _verdict_url(
                full_value or raw, restored)
        return verdict, reason

    def _uncovered_for(self, page, param):
        """本次检测的未覆盖参数列表：入口登记了跳过原因即未覆盖。"""
        uncovered = []
        for item in self._entries.get(page, []):
            if item["param"] == param and item.get("reason"):
                uncovered.append({
                    "page": page,
                    "param": item["param"],
                    "stored": item["stored"],
                    "reason": item["reason"],
                })
        return uncovered

    def _param_counts(self):
        counts = {}
        for item in self._last_findings:
            counts[item["param"]] = counts.get(item["param"], 0) + 1
        return counts


# ---------------------------------------------------------------------------
# 模块级默认引擎与函数入口
# ---------------------------------------------------------------------------

_engine = ReflectEngine()


def load(page, body):
    """登记页面正文。"""
    return _engine.load(page, body)


def unload(page):
    """移除已登记页面及其入口。"""
    return _engine.unload(page)


def add_entry(page, param, stored=False, reason=""):
    """登记反射型 / 存储型检测入口；带 reason 表示该入口暂时跳过。"""
    return _engine.add_entry(page, param, stored=stored, reason=reason)


def remove_entry(page, param):
    """移除检测入口。"""
    return _engine.remove_entry(page, param)


def check(url, param, payload=None):
    """检测单个参数：返回命中位置、上下文、payload、结论与还原过程。

    第一个参数沿用 check(url, param) 的新入口命名，本工具以页面名
    （或 URL 字符串）作为页面登记键，含义与 load() 的 page 一致。
    """
    return _engine.check(url, param, payload=payload)


def check_all(payload=None):
    """对全部已登记入口逐页检测。"""
    return _engine.check_all(payload=payload)


def report():
    """按页面汇总最近一次检测的结论。"""
    return _engine.report()


def reset():
    """清空默认引擎的全部状态。"""
    _engine.reset()
