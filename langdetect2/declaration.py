"""语言声明来源：HTTP 响应头与 HTML 页面声明。

优先级（从高到低，但都只作为"声明"与正文证据交叉验证）：
1. HTTP Content-Language 响应头
2. html/body/元素上的 lang / xml:lang 属性
3. meta http-equiv="content-language"
"""

from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "head", "title", "meta", "link"}


def normalize_lang(raw):
    """en-US -> en，ZH_HANT -> zh，空值 -> None。"""
    if raw is None:
        return None
    lang = str(raw).strip().replace("_", "-").lower()
    if not lang:
        return None
    return lang.split("-")[0] or None


def parse_header_langs(headers):
    """解析 Content-Language 头，可能是多值（逗号分隔）。"""
    if not headers:
        return []
    raw = None
    for key, value in headers.items():
        if key.lower() == "content-language":
            raw = value
            break
    if not raw:
        return []
    langs = []
    for part in str(raw).split(","):
        lang = normalize_lang(part)
        if lang and lang not in langs:
            langs.append(lang)
    return langs


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        # 每个元素入栈一个 lang（无 lang 属性则为 None）
        self._lang_stack = []
        self._chunks = []       # (lang, text)
        self.meta_langs = []
        self._skip_depth = 0

    def _current_lang(self):
        for lang in reversed(self._lang_stack):
            if lang is not None:
                return lang
        return None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            if tag == "meta":
                equiv = (attrs.get("http-equiv") or "").lower()
                if equiv == "content-language":
                    lang = normalize_lang(attrs.get("content"))
                    if lang and lang not in self.meta_langs:
                        self.meta_langs.append(lang)
            return
        lang = normalize_lang(attrs.get("lang") or attrs.get("xml:lang"))
        self._lang_stack.append(lang)

    def handle_startendtag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            equiv = (attrs.get("http-equiv") or "").lower()
            if equiv == "content-language":
                lang = normalize_lang(attrs.get("content"))
                if lang and lang not in self.meta_langs:
                    self.meta_langs.append(lang)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._lang_stack:
            self._lang_stack.pop()

    def handle_data(self, data):
        if self._skip_depth or not data.strip():
            return
        self._chunks.append((self._current_lang(), data))

    def close(self):
        super().close()

    def blocks(self):
        """合并相邻同 lang 文本，规整空白后输出段落。"""
        merged = []
        for lang, text in self._chunks:
            clean = " ".join(text.split())
            if not clean:
                continue
            if merged and merged[-1]["lang"] == lang:
                merged[-1]["text"] += " " + clean
            else:
                merged.append({"text": clean, "lang": lang})
        return merged


def parse_html(html):
    """解析 HTML，返回 (段落列表 [{text, lang}], 声明语言列表)。"""
    parser = _PageParser()
    parser.feed(html or "")
    parser.close()
    blocks = parser.blocks()
    declared = []
    for block in blocks:
        if block["lang"] and block["lang"] not in declared:
            declared.append(block["lang"])
    for lang in parser.meta_langs:
        if lang not in declared:
            declared.append(lang)
    return blocks, declared
