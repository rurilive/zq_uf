"""内容规则：匹配真实的危险调用和混淆特征，按权重打分而不是见词就报。"""

import re

# 真正可执行外部输入的危险函数
SINKS = (
    "eval",
    "assert",
    "system",
    "exec",
    "shell_exec",
    "passthru",
    "proc_open",
    "popen",
    "pcntl_exec",
    "create_function",
    "call_user_func",
    "call_user_func_array",
)

SUPERGLOBALS = ("_GET", "_POST", "_COOKIE", "_REQUEST", "_SERVER", "_FILES")

_SINK_PATTERN = re.compile(r"(?<![A-Za-z0-9_$])(?P<sink>" + "|".join(SINKS) + r")\s*\(")
_SUPERGLOBAL_PATTERN = re.compile(r"\$(?:" + "|".join(SUPERGLOBALS) + r")\b")
_DYNAMIC_CALL = re.compile(r"(\$[A-Za-z_]\w*)\s*\((?!\s*\))")
_VARIABLE_VARIABLE = re.compile(r"\$\$[A-Za-z_]")
_PREG_RE = re.compile(r"preg_replace\s*\(\s*(['\"])[^'\"]*/[a-z]*e[a-z]*\1", re.IGNORECASE)
_INCLUDE = re.compile(r"\b(?:include|require)(?:_once)?\s*(?:\(\s*)?\$")
_ASSIGNMENT = re.compile(
    r"\$([A-Za-z_]\w*)\s*=\s*((?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")(?:\s*\.\s*(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"))*)"
)
_WRAPPER_NAME = re.compile(
    r"\b(base64_decode|gzinflate|gzuncompress|str_rot13|strrev|urldecode|hex2bin)\s*\("
)
_BASE64_LITERAL = re.compile(r"['\"]([A-Za-z0-9+/]{24,}={0,2})['\"]")
_CONCAT_OBFUSCATION = re.compile(r"['\"][^'\"\n]{1,8}['\"]\s*\.\s*['\"][^'\"\n]{1,8}['\"]")
_CHR_CALL = re.compile(r"\bchr\s*\(\s*\d+\s*\)")
_SUSPICIOUS_PARAM = re.compile(
    r"(?:cmd|exec|shell|command|pass|passwd|payload|func|do|act|run)\b", re.IGNORECASE
)


def strip_php_comments(source):
    """去掉 PHP 注释但保留换行，保证行号与原文一致；字符串里的 # // 不动。"""
    out = []
    i, n = 0, len(source)
    quote = None
    while i < n:
        char = source[i]
        if quote is not None:
            out.append(char)
            if char == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in ("'", '"'):
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "#" or (
            char == "/" and i + 1 < n and source[i + 1] == "/"
        ):
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if char == "/" and i + 1 < n and source[i + 1] == "*":
            while i < n and not (source[i] == "*" and i + 1 < n and source[i + 1] == "/"):
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            out.extend((" ", " "))
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _assignment_map(restored_lines):
    """收集 $x = '字符串'（含折叠后的拼接串），用于还原间接调用。"""
    mapping = {}
    for line in restored_lines:
        for match in _ASSIGNMENT.finditer(line):
            value = re.sub(r"^\s*['\"]|['\"]\s*$", "", match.group(2))
            value = re.sub(r"['\"]\s*\.\s*['\"]", "", value)
            mapping[match.group(1)] = value
    return mapping


def _code_only(line):
    """剥掉注释和空白，用于判断一行是否真的被还原改写。"""
    return re.sub(r"\s+", "", strip_php_comments(line))


def analyze_content(stripped_lines, restored_lines):
    """对逐行还原后的源码跑规则。

    matches 按 (行, 规则) 去重后返回（同文件合并成一条 finding），
    total_occurrences 统计原始命中次数，重复出现的同类规则也计入。
    """
    mapping = _assignment_map(restored_lines)
    seen, matches = set(), []
    total_occurrences = 0

    def add(line, rule, weight, target, text, restored):
        nonlocal total_occurrences
        total_occurrences += 1
        key = (line, rule)
        if key in seen:
            return False
        seen.add(key)
        matches.append(
            {
                "line": line,
                "rule": rule,
                "weight": weight,
                "target": target,
                "text": text.strip()[:200],
                "restored": restored,
            }
        )
        return True

    for number, (raw_line, restored) in enumerate(
        zip(stripped_lines, restored_lines), start=1
    ):
        user_input = bool(_SUPERGLOBAL_PATTERN.search(restored))
        # 还原产物保留注释，判定“是否靠还原才出现”时只比较代码字符
        changed = _code_only(restored) != _code_only(raw_line)

        for sink in _SINK_PATTERN.finditer(restored):
            name = sink.group("sink")
            if changed and not _SINK_PATTERN.search(raw_line):
                rule, weight = "restored_sink", 45
            elif user_input:
                rule, weight = "sink_with_user_input", 55
            else:
                rule, weight = "dangerous_sink", 30
            add(number, rule, weight, name, restored, changed)

        if _SUPERGLOBAL_PATTERN.search(restored):
            for call in re.finditer(
                r"\$(?:" + "|".join(SUPERGLOBALS) + r")\s*(?:\[[^\]]*\])?\s*\(",
                restored,
            ):
                add(number, "superglobal_direct_call", 55, call.group(0), restored, changed)

        for call in _DYNAMIC_CALL.finditer(restored):
            variable = call.group(1).lstrip("$")
            target = mapping.get(variable)
            if target:
                if target in SINKS:
                    weight = 45 if user_input else 40
                    rule = "dynamic_sink_call"
                else:
                    weight, rule = 20, "dynamic_call"
                add(number, rule, weight, target, restored, changed)
            elif user_input:
                add(number, "dynamic_call_with_input", 35, variable, restored, changed)
            else:
                add(number, "dynamic_call", 20, variable, restored, changed)

        if _PREG_RE.search(restored):
            add(number, "preg_replace_e_modifier", 45, "preg_replace", restored, changed)

        if _INCLUDE.search(restored):
            add(
                number,
                "dynamic_include",
                45 if user_input else 35,
                "include/require",
                restored,
                changed,
            )

        if _WRAPPER_NAME.search(restored):
            add(number, "encoding_wrapper", 20, "encoding function", restored, changed)
        if _CONCAT_OBFUSCATION.search(restored):
            add(number, "concat_obfuscation", 10, "string split", restored, changed)
        if _CHR_CALL.search(restored):
            add(number, "chr_obfuscation", 15, "chr()", restored, changed)
        if _VARIABLE_VARIABLE.search(restored):
            add(number, "variable_variable", 10, "$$", restored, changed)

        for token in _BASE64_LITERAL.finditer(restored):
            add(number, "base64_like_string", 15, token.group(1)[:24] + "…", restored, changed)

    return matches, total_occurrences


def analyze_access(access):
    """从访问日志条目里提取行为信号。"""
    signals = []
    for entry in access or []:
        if not isinstance(entry, dict):
            continue
        method = str(entry.get("method", "")).upper()
        query = str(entry.get("query", ""))
        post = str(entry.get("post", ""))
        body = f"{query}&{post}"
        keys = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=", body)
        if method in ("POST", "PUT") and _SUSPICIOUS_PARAM.search(body):
            signals.append({"type": "suspicious_request", "line": entry, "weight": 10})
        if any(key.lower() in {"cmd", "exec", "shell", "command", "payload"} for key in keys):
            signals.append({"type": "suspicious_request", "line": entry, "weight": 10})
        if entry.get("status") in (404, 403, 500):
            signals.append({"type": "error_status", "line": entry, "weight": 5})
    # 同样的信号只算一次分，日志原文全部保留
    deduped = {}
    for signal in signals:
        deduped.setdefault(signal["type"], signal)
    return list(deduped.values())
