"""密钥形态识别：已知前缀、高熵字符串、赋值上下文，与状态管理解耦。

只负责“这段文本里哪些值像密钥、可信度多高”，不维护任何跨扫描状态。
"""

import math
import re

# --- 位置类型：代码 / 配置 / 构建产物 -------------------------------------

_CONFIG_SUFFIXES = {
    ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".properties", ".env", ".xml", ".plist",
}
_BUILD_SUFFIXES = {".min.js", ".map", ".pyc", ".class", ".jar", ".war", ".zip"}
_BUILD_PATH_PARTS = (
    "dist/", "build/", "target/", "out/", "node_modules/",
    "vendor/", "artifacts/", "__pycache__/",
)


def classify_location(file_name):
    """把文件归到 code / config / build 三类位置。"""
    lowered = file_name.lower()
    if any(part in lowered for part in _BUILD_PATH_PARTS):
        return "build"
    if lowered.endswith(tuple(_BUILD_SUFFIXES)):
        return "build"
    if lowered.endswith(tuple(_CONFIG_SUFFIXES)):
        return "config"
    return "code"


# --- 已知密钥前缀规则 -------------------------------------------------------

_KNOWN_PREFIXES = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws-access-key-id"),
    (re.compile(r"ASIA[0-9A-Z]{16}"), "aws-session-token"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "google-api-key"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "github-personal-access-token"),
    (re.compile(r"gho_[A-Za-z0-9]{36}"), "github-oauth-token"),
    (re.compile(r"gh[usr]_[A-Za-z0-9]{36,}"), "github-token"),
    (re.compile(r"glpat-[A-Za-z0-9\-_]{20}"), "gitlab-personal-access-token"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "slack-token"),
    (re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), "stripe-secret-key"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "openai-api-key"),
    (re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ), "jwt"),
]
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z ]+?)?PRIVATE KEY-----.*?-----END (?:[A-Z ]+?)?PRIVATE KEY-----",
    re.DOTALL,
)

# --- 赋值与候选提取 ---------------------------------------------------------

_CONTEXT_KEYWORDS = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "access_key", "secret_key", "client_secret", "auth_token",
    "private_key", "credential",
)
_ASSIGN_RE = re.compile(
    r"(?i)(?:export\s+)?([A-Za-z_][\w.\-]*)\s*[:=]\s*(.*?)\s*$"
)
_QUOTED_RE = re.compile(r"""(?:"([^"\n]{6,})"|'([^'\n]{6,})')""")
_BARE_TOKEN_RE = re.compile(r"[A-Za-z0-9_./+:=-]{20,}")
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_RE = re.compile(r"(?:0x)?[0-9a-fA-F]{16,}")
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:x+|y+|your[_\- ][\w ]+|changeme|change[_\-]me|todo|tbd|"
    r"dummy|example|sample|none|null|n/?a|password|secret|token|key)$"
)
_TEMPLATE_RE = re.compile(r"(\$\{[^}]+\}|\{\{[^}]+\}\}|<%[^%]+%>|<[A-Z_ ]{4,}>)")
_COMMENT_RE = re.compile(r"^\s*(?:#|//|--|/\*)")


def shannon_entropy(value):
    """香农熵（bit/字符），衡量字符分布的无序程度。"""
    if not value:
        return 0.0
    counts = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def looks_random(value):
    """高熵且字符集多样：长度、数字+字母、大小写混合共同判断。"""
    if len(value) < 16 or " " in value:
        return False
    if not re.search(r"[0-9]", value) or not re.search(r"[A-Za-z]", value):
        return False
    # 单一大写/小写串容易是标识符或哈希片段，要求混合大小写，超长放宽。
    if len(value) < 24 and not (
        re.search(r"[a-z]", value) and re.search(r"[A-Z]", value)
    ):
        return False
    return shannon_entropy(value) >= 3.7


def _is_placeholder(value):
    if _TEMPLATE_RE.search(value):
        return True
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def _prefix_match(value):
    for pattern, type_name in _KNOWN_PREFIXES:
        match = pattern.search(value)
        if match:
            return match.group(0), type_name
    return None, None


def _keyword_in(key):
    normalized = key.lower().replace("-", "_")
    return any(word in normalized for word in _CONTEXT_KEYWORDS)


def _evaluate(value, has_keyword, is_comment):
    """返回 (confidence, type)；不像密钥则返回 (None, None)。"""
    if not value or _is_placeholder(value):
        return None, None
    matched, type_name = _prefix_match(value)
    if matched:
        # 前缀是强形态证据，注释里出现也仍然是真实泄露。
        return "high", type_name
    random = looks_random(value)
    if has_keyword:
        if random:
            # 注释里的高熵值降一级，降低历史代码注释的误报。
            return ("medium" if is_comment else "high"), "generic-secret"
        return "low", "generic-secret"
    # 无密钥上下文：只收高熵候选，裸 UUID/十六进制哈希不收。
    if random and not _UUID_RE.fullmatch(value) and not _HEX_RE.fullmatch(value):
        return "medium", "generic-secret"
    return None, None


def _line_candidates(line):
    """提取一行里的 (value, keyword) 候选，同行去重。"""
    candidates = []
    seen = set()

    def add(value, keyword):
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            candidates.append((value, keyword))

    assign = _ASSIGN_RE.search(line)
    if assign:
        key, rest = assign.group(1), assign.group(2)
        keyword = key if _keyword_in(key) else None
        quoted = _QUOTED_RE.search(rest)
        if quoted:
            add(next(g for g in quoted.groups() if g), keyword)
        else:
            token = re.match(r"[A-Za-z0-9_./+:=-]+", rest)
            if token:
                add(token.group(0), keyword)
    for match in _QUOTED_RE.finditer(line):
        add(next(g for g in match.groups() if g), None)
    for token in _BARE_TOKEN_RE.findall(line):
        add(token, None)
    return candidates


def scan_text(text):
    """扫描单文件文本，返回形态层发现（未合并、无状态）。"""
    findings = []
    seen = set()

    for match in _PRIVATE_KEY_RE.finditer(text):
        block = match.group(0)
        line = text[: match.start()].count("\n") + 1
        key = (line, block)
        if key not in seen:
            seen.add(key)
            findings.append({
                "value": block,
                "line": line,
                "keyword": "private_key",
                "confidence": "high",
                "type": "private-key",
                "context": block.splitlines()[0][:120],
                "is_comment": False,
            })

    for number, line in enumerate(text.splitlines(), start=1):
        is_comment = bool(_COMMENT_RE.match(line))
        for value, keyword in _line_candidates(line):
            confidence, type_name = _evaluate(value, bool(keyword), is_comment)
            if confidence is None or (number, value) in seen:
                continue
            seen.add((number, value))
            findings.append({
                "value": value,
                "line": number,
                "keyword": keyword,
                "confidence": confidence,
                "type": type_name,
                "context": line.strip()[:120],
                "is_comment": is_comment,
            })
    return findings
