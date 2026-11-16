"""密钥形态识别。

只负责"在一段文本里找出疑似密钥并给出可信程度"，不保存任何状态。
判断依据三类信号，按提示词要求一起看：

1. 已知前缀：云厂商 / SaaS 密钥有固定形态（如 AKIA、ghp_、xoxb-）；
2. 高熵字符串：长度足够、字符分布足够随机，且不像普通词或路径；
3. 上下文：变量名 / 配置键里带 password、token、api_key 等字样。

同一个位置可能被多个信号命中，统一取最高可信程度。
"""

import math
import re

# 可信程度（中文枚举，report/scan 直接透出）。
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

# 上下文关键字：等号 / 冒号左侧出现这些词，说明右边"应该"是个密钥。
CONTEXT_KEYWORDS = (
    "password", "passwd", "pwd",
    "secret", "token", "apikey", "api_key",
    "access_key", "accesskey", "secret_key", "secretkey",
    "auth", "credential", "private_key", "privatekey",
    "client_secret", "app_secret",
)

# 已知密钥前缀：(前缀, 类型, 最小总长度)。命中即高可信。
KNOWN_PREFIXES = (
    ("AKIA", "aws_access_key_id", 20),          # AWS access key id
    ("ASIA", "aws_temp_access_key", 20),        # AWS 临时密钥
    ("ghp_", "github_personal_token", 30),      # GitHub PAT
    ("gho_", "github_oauth_token", 30),
    ("ghu_", "github_user_token", 30),
    ("ghs_", "github_app_token", 30),
    ("ghr_", "github_refresh_token", 30),
    ("github_pat_", "github_fine_grained_pat", 30),
    ("glpat-", "gitlab_pat", 20),
    ("xoxb-", "slack_bot_token", 20),
    ("xoxp-", "slack_user_token", 20),
    ("xoxa-", "slack_app_token", 20),
    ("xoxr-", "slack_refresh_token", 20),
    ("sk-ant-", "anthropic_api_key", 30),
    ("sk-proj-", "openai_project_key", 20),
    ("sk-", "openai_api_key", 20),
    ("AIza", "google_api_key", 30),
    ("ya29.", "google_oauth_token", 20),
    ("hooks.slack.com/services/", "slack_webhook", 20),
    ("RSA ", "pem_private_key", 0),             # PEM 体在下面单独处理
)

# PEM 私钥块，如 -----BEGIN PRIVATE KEY----- ... -----END ...-----
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]+?-----END [A-Z ]*PRIVATE KEY-----"
)

# 赋值语句：key = value / key: value，右侧可带引号或不带引号。
_ASSIGN_RE = re.compile(
    r"""
    (?P<key>[A-Za-z_][\w.\-]*)\s*[:=]\s*      # 左侧标识符 + 分隔符
    (?P<value>'[^'\n]*'|"[^"\n]*"|[^\s#'",;]+) # 引号值或裸值
    """,
    re.VERBOSE,
)

# 从一行里抠"长得像密钥"的裸 token（用于没有关键字、没有赋值的场景）。
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_\-.]{12,}")

# 占位符 / 模板变量 / 环境变量引用，不是真实密钥。
_PLACEHOLDER_RE = re.compile(
    r"""
    ^(
      x{3,}                       # xxxxxx
    | \$\{.*\}                    # ${ENV}
    | \$[A-Z_][A-Z0-9_]*          # $ENV
    | \{\{.*\}\}                  # {{ var }}
    | <.*>                        # <your-token>
    | %[A-Z_]+%                   # %ENV%
    | example(secret|token|key)?  # example / examplesecret
    | your[_-]?(secret|token|key|password)?
    | dummy.*
    )$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# 一眼假的示例值，即使形态像也降级/忽略。
_DUMMY_VALUES = frozenset(
    (
        "hunter2", "changeme", "password", "secret", "token",
        "123456", "12345678", "qwerty", "admin", "root",
        "testtest", "foobar", "your_token_here",
    )
)

# 构建产物 / 依赖目录：路径里出现这些段就归为 build。
_BUILD_MARKERS = (
    "dist/", "build/", "target/", "out/", "node_modules/",
    ".next/", ".nuxt/", "vendor/", "bin/", "obj/",
    "__pycache__/", ".cache/", "coverage/",
)

# 配置文件后缀。
_CONFIG_SUFFIXES = (
    ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".properties", ".plist", ".config", ".pem", ".key",
    ".crt", ".cer", ".p12", ".pfx",
)
_CONFIG_NAMES = ("dockerfile", "makefile", ".env")


def shannon_entropy(text):
    """Shannon 熵（bits/char）。字符种类越少、分布越偏，熵越低。"""
    if not text:
        return 0.0
    counts = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(text)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def _class_runs(text):
    """统计字符类别（小写/大写/数字/符号）数量，用于区分随机串和单词。"""
    classes = 0
    if re.search(r"[a-z]", text):
        classes += 1
    if re.search(r"[A-Z]", text):
        classes += 1
    if re.search(r"[0-9]", text):
        classes += 1
    if re.search(r"[^A-Za-z0-9]", text):
        classes += 1
    return classes


def _is_placeholder(value):
    if value.lower() in _DUMMY_VALUES:
        return True
    if _PLACEHOLDER_RE.match(value):
        return True
    # 全是同一个字符重复（aaaaaa / 000000）。
    if len(set(value)) <= 1:
        return True
    return False


def _looks_like_path_or_word(token):
    """过滤文件路径、版本号、URL、UUID 等"高字符数但不是密钥"的东西。"""
    lowered = token.lower()
    # URL / data URI：协议头 + 主机/路径，整体不是一个密钥。
    if re.match(r"[a-z][a-z0-9+.\-]*://", lowered) or lowered.startswith("data:"):
        return True
    # UUID 8-4-4-4-12。
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", token):
        return True
    if "/" in token and ("." in token or token.endswith("/")):
        # a/b/c.py 这类路径
        if re.search(r"\.[A-Za-z0-9]{1,5}$", token):
            return True
    # 纯版本号 1.2.3 / 2.11.0
    if re.fullmatch(r"\d+(\.\d+)+", token):
        return True
    # 全小写、无数字无符号的长单词（英文句子里的普通词）。
    if token.islower() and not re.search(r"[0-9_\-+/]", token):
        return True
    return False


def _entropy_verdict(value, has_context=False):
    """高熵信号。返回 (confidence, type) 或 None。

    has_context：赋值键带密钥字样。裸 40 位 hex 很可能是 git 提交哈希，
    默认只给 low；有密钥上下文时才升到 medium。
    """
    if len(value) < 16 or _is_placeholder(value):
        return None
    if _looks_like_path_or_word(value):
        return None
    entropy = shannon_entropy(value)
    classes = _class_runs(value)
    if classes < 2:
        return None

    # base64 / 混合字符随机串：长度够、熵够高、至少 3 种字符类别。
    if len(value) >= 24 and entropy >= 4.0 and classes >= 3:
        return HIGH, "high_entropy_secret"
    # 长 hex（32/40 位十六进制密钥或提交哈希）。
    if re.fullmatch(r"[0-9a-fA-F]{24,}", value) and entropy >= 3.0:
        return (MEDIUM, "hex_secret") if has_context else (LOW, "hex_string")
    # 其它混合字符长串，门槛抬高一点。
    if len(value) >= 20 and entropy >= 3.5 and classes >= 3:
        return MEDIUM, "high_entropy_secret"
    return None


def _prefix_verdict(value):
    """已知前缀信号。命中即高可信；多个前缀命中时取最长（最特异）的那个。"""
    best = None
    for prefix, stype, min_len in KNOWN_PREFIXES:
        if value.startswith(prefix) and len(value) >= min_len:
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, stype, min_len)
    if best is None:
        return None
    return HIGH, best[1]


def _context_hit(key):
    """赋值左侧是否带密钥字样。"""
    lowered = key.lower()
    for word in CONTEXT_KEYWORDS:
        if word in lowered:
            return word
    return None


def location_kind(file_name):
    """位置分类：build / config / code，按路径与后缀判断。

    构建产物优先级最高：dist 里的 .js/.json 仍是构建产物，不是源码/配置。
    """
    lowered = file_name.lower()
    if any(marker in lowered for marker in _BUILD_MARKERS):
        return "build"
    base = lowered.rsplit("/", 1)[-1]
    if base in _CONFIG_NAMES or any(lowered.endswith(s) for s in _CONFIG_SUFFIXES):
        return "config"
    return "code"


def _unquote(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1], True
    return raw, False


def scan_text(file_name, text, known_values=()):
    """扫描单个文件内容，返回命中列表。

    每条命中：{file, line, key, value, confidence, type, context, quoted, kind}。
    同一 (line, value) 多个信号只保留一条，可信程度取最高。
    known_values：已经在别处确认过的密钥内容，本文件里再出现即按"引用"补抓，
    这样裸值调用（如 call(samevalue)）也能进入使用痕迹。
    """
    hits = {}  # (line, value) -> hit
    kind = location_kind(file_name)
    known_values = set(known_values)

    # 1) PEM 私钥块（跨行，整体作为一个密钥）。记录其覆盖的行，行扫描时跳过，
    #    否则 base64 体会被高熵规则重复报一遍。
    pem_lines = set()
    for match in _PEM_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        end_line = text.count("\n", 0, match.end()) + 1
        pem_lines.update(range(line, end_line + 1))
        value = match.group(0).strip()
        key = (line, value)
        hits[key] = {
            "file": file_name,
            "line": line,
            "key": "private_key",
            "value": value,
            "confidence": HIGH,
            "type": "pem_private_key",
            "context": "private_key",
            "quoted": False,
            "in_comment": False,
            "kind": kind,
        }

    for number, raw_line in enumerate(text.splitlines(), start=1):
        if number in pem_lines:
            continue
        # 保留注释内容：注释里残留的密钥也是真实泄露面；仅记录是否整行为注释。
        line = raw_line
        if not line.strip():
            continue
        is_comment = line.lstrip().startswith("#")

        # 2) 赋值语句：key = value / key: value，结合上下文判断。
        for match in _ASSIGN_RE.finditer(line):
            key_name = match.group("key")
            raw_value = match.group("value")
            value, quoted = _unquote(raw_value)
            if not value:
                continue
            context = _context_hit(key_name)
            # 模板/环境变量/尖括号占位符结构上就不是密钥，任何信号都不报。
            structural_placeholder = bool(_PLACEHOLDER_RE.match(value))
            # 人尽皆知的示例/弱口令值，即使出现在赋值里也不是真实密钥。
            known_dummy = value.lower() in _DUMMY_VALUES
            if structural_placeholder or known_dummy:
                continue

            signal = None
            stype = None
            prefix_hit = _prefix_verdict(value)
            if prefix_hit:
                signal, stype = prefix_hit
            else:
                ent = _entropy_verdict(value, has_context=bool(context))
                if ent:
                    signal, stype = ent
            if context and signal is None:
                # 只有上下文、形态不强：低可信，交人工确认（注释里的残留密钥也走这里）。
                signal, stype = LOW, "contextual_secret"
            if signal is None:
                continue
            # 上下文 + 形态信号互相加成：medium 提到 high。
            if context and signal == MEDIUM:
                signal = HIGH
            record_key = (number, value)
            existing = hits.get(record_key)
            if existing is None or _rank(signal) > _rank(existing["confidence"]):
                hits[record_key] = {
                    "file": file_name,
                    "line": number,
                    "key": key_name,
                    "value": value,
                    "confidence": signal,
                    "type": stype,
                    "context": context,
                    "quoted": quoted,
                    "in_comment": is_comment,
                    "kind": kind,
                }

        # 3) 自由 token：没有赋值/关键字，但形态像密钥（高熵或已知前缀）。
        for token_match in _TOKEN_RE.finditer(line):
            value = token_match.group(0)
            if _is_placeholder(value) or _looks_like_path_or_word(value):
                continue
            signal = _prefix_verdict(value) or _entropy_verdict(value)
            if signal is None:
                continue
            confidence, stype = signal
            record_key = (number, value)
            existing = hits.get(record_key)
            if existing is not None and _rank(existing["confidence"]) >= _rank(confidence):
                continue
            hits[record_key] = {
                "file": file_name,
                "line": number,
                "key": None,
                "value": value,
                "confidence": confidence,
                "type": stype,
                "context": None,
                "quoted": False,
                "in_comment": is_comment,
                "kind": kind,
            }

        # 4) 已知密钥引用：形态不随机、但内容是别处已确认的密钥，补记使用痕迹。
        for value in known_values:
            if value and value in line:
                record_key = (number, value)
                if record_key in hits:
                    continue
                hits[record_key] = {
                    "file": file_name,
                    "line": number,
                    "key": None,
                    "value": value,
                    "confidence": LOW,
                    "type": "secret_reference",
                    "context": None,
                    "quoted": False,
                    "in_comment": is_comment,
                    "kind": kind,
                }

    return list(hits.values())


def _rank(confidence):
    return {LOW: 1, MEDIUM: 2, HIGH: 3}.get(confidence, 0)
