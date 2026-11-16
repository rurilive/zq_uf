"""按参数和响应差异做注入判定。

与旧版"每个固定 payload 记一条"不同，这里对每个参数收齐多次尝试后只给一条结论：

* error    —— 响应命中了某个数据库的报错特征（不同数据库分开判断）
* boolean  —— 真/假条件 payload 的响应存在可对照的差异（布尔盲注）
* time     —— 异常 payload 的响应耗时显著高于基线（时间盲注）
* pending  —— 有可疑迹象但证据不足，标为待确认

check(url, params) 返回可疑参数、判定依据、请求样例和复现步骤；report() 按接口
汇总结论；mark_false_positive(url, param, reason) 给误报打标并记录原因。
"""

import copy
import re
import statistics
from urllib.parse import quote

from .signatures import generic_error_signature, match_error_signatures

_TARGETS = {}
_RESULTS = {}
_FALSE_POSITIVES = {}
_MISSING = object()

# 探测 payload：报错、布尔盲注的真/假对照、时间盲注各一组。
ERROR_PAYLOADS = ["'", "\"", "1'", "1\""]
BOOLEAN_PAYLOADS = {
    "true": ["' OR '1'='1", "1' AND '1'='1", "1 AND 1=1"],
    "false": ["' OR '1'='2", "1' AND '1'='2", "1 AND 1=2"],
}
TIME_PAYLOADS = [
    "1' AND SLEEP(5)--",
    "1' AND pg_sleep(5)--",
    "1'; WAITFOR DELAY '0:0:5'--",
]
PAYLOADS = ERROR_PAYLOADS + BOOLEAN_PAYLOADS["true"] + BOOLEAN_PAYLOADS["false"] + TIME_PAYLOADS

# 阈值：低于这些值只标待确认，达到才给确认结论。
TIME_MIN_DELTA = 1.0       # 秒
TIME_MIN_RATIO = 2.5       # 异常耗时 / 基线耗时
TIME_WEAK_DELTA = 0.4
TIME_WEAK_RATIO = 1.5
TIME_ABSOLUTE = 3.0        # 没有基线时，绝对耗时达到该值才考虑
BOOL_MIN_DIFF = 0.10       # 1 - 真/假响应相似度
BOOL_MIN_ASYMMETRY = 0.15  # 真、假响应与基线相似度之差
BOOL_MIN_DIFF_NO_BASE = 0.30
BOOL_WEAK_DIFF = 0.08
BOOL_MIN_CHANGED_CHARS = 8  # 真/假响应的局部差异字符数下限

_SKIP_TIMEOUT = "超时"
_SKIP_REJECTED = "拒绝"
_SKIP_UNSUPPORTED = "协议不支持"
_SKIP_CODES = {
    _SKIP_TIMEOUT: "timeout",
    _SKIP_REJECTED: "rejected",
    _SKIP_UNSUPPORTED: "protocol_unsupported",
}

_TIMEOUT_PATTERNS = ("timeout", "timed out", "deadline exceeded", "超时", "no response")
_REJECT_PATTERNS = (
    "refused", "forbidden", "denied", "blocked", "rejected", "not allowed",
    "connection reset", "reset by peer", "waf", "captcha", "403", "401", "405",
)
_UNSUPPORTED_PATTERNS = (
    "unsupported", "not supported", "unknown protocol", "protocol not", "scheme",
    "ssl", "tls", "ftp://", "file://", "ws://", "wss://", "econnaborted",
)
_TIMED_PAYLOAD_PATTERNS = (
    "sleep(", "sleep (", "pg_sleep", "benchmark(", "waitfor delay",
    "dbms_pipe.receive_message", "randomblob", "heavysql",
)
_TRUE_PAYLOAD_PATTERNS = ("'1'='1", "1=1", "true")
_FALSE_PAYLOAD_PATTERNS = ("'1'='2", "1=2", "false")

_ROLE_KEYS = {
    "baseline": (
        "baseline", "normal", "control", "legit", "clean", "baseline_body",
        "normal_response", "normal_page", "baseline_response",
    ),
    "true": (
        "true", "true_payload", "true_response", "positive", "bool_true",
    ),
    "false": (
        "false", "false_payload", "false_response", "negative", "bool_false",
    ),
    "timed": (
        "timed", "timed_response", "time_response", "time_payload", "delay",
        "sleep", "slow",
    ),
    "abnormal": (
        "abnormal", "abnormal_response", "abnormal_page", "error",
        "error_response", "error_page", "exception_page", "err",
        "exception", "err_response", "injected", "injected_page",
    ),
}
_ROLE_LOOKUP = {name: role for role, names in _ROLE_KEYS.items() for name in names}

_BODY_KEYS = ("body", "response", "response_body", "text", "content", "html", "page", "output")
_ELAPSED_KEYS = (
    "elapsed", "elapsed_time", "response_time", "duration", "latency", "cost",
    "seconds", "ms", "millis", "milliseconds", "took", "delay", "time",
)
_PAYLOAD_KEYS = ("payload", "input", "value", "query", "probe", "param_value")
_STATUS_KEYS = ("status", "status_code", "http_status", "code")
_ERROR_KEYS = ("error", "exception", "err", "failure")
_LIST_KEYS = ("trials", "attempts", "samples", "responses", "observations", "results")
_SKIP_KEYS = ("skip", "skipped", "skip_reason", "uncovered")


# --------------------------------------------------------------------------- #
# 观测值归一化
# --------------------------------------------------------------------------- #

def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _elapsed_seconds(key, value):
    seconds = _to_float(value)
    if seconds is None:
        return None
    if str(key).lower() in ("ms", "millis", "milliseconds"):
        seconds /= 1000.0
    return seconds


def _skip_reason(text):
    """把一段错误/状态描述归到三类跳过原因之一，归不上返回 None。"""
    if text is None:
        return None
    low = str(text).lower()
    if any(word in low for word in _UNSUPPORTED_PATTERNS):
        return _SKIP_UNSUPPORTED
    if any(word in low for word in _TIMEOUT_PATTERNS):
        return _SKIP_TIMEOUT
    if any(word in low for word in _REJECT_PATTERNS):
        return _SKIP_REJECTED
    return None


def _status_skip(status):
    code = _to_float(status)
    if code is None:
        return None
    code = int(code)
    if code in (408, 504):
        return _SKIP_TIMEOUT
    if code in (401, 403, 405, 429, 451):
        return _SKIP_REJECTED
    if code in (501, 505):
        return _SKIP_UNSUPPORTED
    return None


def _make_trial(body=None, payload=None, elapsed=None, status=None,
                error=None, role=None):
    return {
        "body": None if body is None else str(body),
        "payload": payload,
        "elapsed": elapsed,
        "status": status,
        "error": None if error is None else str(error),
        "role": role,
    }


def _trial_from_dict(data):
    body = None
    for key in _BODY_KEYS:
        if key in data and data[key] is not None:
            body = data[key]
            break
    elapsed = None
    for key in _ELAPSED_KEYS:
        if key in data and data[key] is not None:
            elapsed = _elapsed_seconds(key, data[key])
            if elapsed is not None:
                break
    payload = None
    for key in _PAYLOAD_KEYS:
        if key in data and data[key] is not None:
            payload = data[key]
            break
    status = None
    for key in _STATUS_KEYS:
        if key in data and data[key] is not None:
            status = data[key]
            break
    error = None
    for key in _ERROR_KEYS:
        if key in data and data[key] is not None:
            error = data[key]
            break
    role = data.get("role")
    if role not in _ROLE_KEYS:
        role = _ROLE_LOOKUP.get(role)
    return _make_trial(body, payload, elapsed, status, error, role)


def _looks_like_trial(data):
    if any(key in data for key in _BODY_KEYS + _PAYLOAD_KEYS + _STATUS_KEYS):
        return True
    if any(key in data and _to_float(data[key]) is not None for key in _ELAPSED_KEYS):
        return True
    if any(key in data for key in _ERROR_KEYS) and len(data) <= 3:
        return True
    return False


def _payload_role(payload):
    if payload is None:
        return None
    text = str(payload).lower()
    compact = re.sub(r"\s+", "", text)
    if any(word in text for word in _TIMED_PAYLOAD_PATTERNS):
        return "timed"
    if any(word in compact for word in _TRUE_PAYLOAD_PATTERNS):
        return "true"
    if any(word in compact for word in _FALSE_PAYLOAD_PATTERNS):
        return "false"
    return None


def _infer_role(trial):
    if trial["role"] in _ROLE_KEYS:
        return trial["role"]
    return _payload_role(trial["payload"])


def _normalize_trial(item, role=None):
    if isinstance(item, dict):
        trial = _trial_from_dict(item)
        if role is not None and trial["role"] is None:
            trial["role"] = role
        return trial
    if isinstance(item, (list, tuple)):
        payload = item[0] if len(item) > 0 else None
        body = item[1] if len(item) > 1 else None
        elapsed = _to_float(item[2]) if len(item) > 2 else None
        trial = _make_trial(body, payload, elapsed, None, None, role)
        if trial["role"] is None:
            trial["role"] = _payload_role(payload)
        return trial
    return _make_trial(item, None, None, None, None, role)


def _normalize_observation(value):
    """把一个参数登记的多形态观测值归一成 trial 列表 + 跳过原因。

    支持字符串、字典、列表，以及 (payload, body[, elapsed]) 元组。
    """
    trials, skip = _normalize_raw(value)

    # 逐条剔除被拒绝/超时/协议不支持的尝试：这类请求没有真正进入参数处理逻辑。
    # 普通 error 字段（非传输类错误）视为异常页面，保留为观测。
    kept = []
    first_skip = skip
    for trial in trials:
        reason = _status_skip(trial["status"])
        if reason is None:
            transport = _skip_reason(trial["error"])
            if transport is not None:
                reason = transport
            elif trial["error"] and trial["body"] is None and trial["elapsed"] is None:
                trial["body"] = trial["error"]
        if reason is None and trial["body"] is None and trial["elapsed"] is None:
            reason = _SKIP_TIMEOUT
        if reason is not None:
            if first_skip is None:
                first_skip = reason
            continue
        kept.append(trial)

    if kept:
        return kept, skip
    return [], first_skip or _SKIP_UNSUPPORTED


def _normalize_raw(value):
    trials = []
    skip = None

    if value is None or value is _MISSING:
        return trials, _SKIP_TIMEOUT

    if isinstance(value, str):
        return [_make_trial(value)], None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [_make_trial(str(value))], None

    if isinstance(value, (list, tuple)):
        for item in value:
            trial = _normalize_trial(item)
            if trial["error"] and trial["body"] is None and trial["elapsed"] is None:
                # 非传输类的 error 文案当作异常页面，不能因为只填了 error 就丢观测。
                if _skip_reason(trial["error"]) is None:
                    trial["body"] = trial["error"]
            trials.append(trial)
        return [t for t in trials if t["body"] is not None or t["elapsed"] is not None], skip

    if not isinstance(value, dict):
        return [], _SKIP_UNSUPPORTED

    # 显式声明跳过
    for key in _SKIP_KEYS:
        if key in value:
            if not value[key]:
                break  # {"skip": False} 表示不跳过，继续按普通观测解析
            skip = _skip_reason(value[key]) or _skip_reason(str(value[key]))
            return [], skip or _SKIP_UNSUPPORTED
    if value.get("reason") and not _looks_like_trial(value):
        skip = _skip_reason(value["reason"]) or _SKIP_UNSUPPORTED
        return [], skip

    # 批量试验
    for key in _LIST_KEYS:
        if key in value:
            return _normalize_observation(value[key])

    # 全是角色名的字典按角色解析，必须早于单次试验判断：
    # {"error": "<页面>", "true": ...} 里的 error 是角色，不是错误字段。
    role_only = (len(value) >= 2
                 and all(str(key).lower() in _ROLE_LOOKUP for key in value))
    if role_only:
        return _role_trials(value)

    # 单次试验
    if _looks_like_trial(value):
        trial = _trial_from_dict(value)
        # 被拒绝/超时/协议不支持的请求没有真正打到参数处理逻辑，
        # 即使响应体有内容也不能当作有效观测。
        reason = _status_skip(trial["status"]) or _skip_reason(trial["error"])
        if reason:
            return [], reason
        if trial["body"] is None and trial["elapsed"] is None:
            if trial["error"]:
                # 非传输类 error 文案当作异常页面，交给报错特征判定。
                trial["body"] = trial["error"]
            else:
                return [], _SKIP_TIMEOUT
        return [trial], None

    # 以角色命名的多次响应，如 {"normal": ..., "true": ..., "false": ...}。
    # 认不出的键不能丢：当成按 payload 登记的响应一并保留，避免漏掉报错证据。
    return _role_trials(value)


def _role_trials(value):
    role_trials = []
    unparsed = []
    for key, item in value.items():
        role = _ROLE_LOOKUP.get(str(key).lower())
        if role is None:
            unparsed.append((key, item))
            continue
        # 同一个角色下可以登记多次尝试：列表元素若是字典/元组，就逐个展开；
        # 否则整个列表按 (payload, body[, elapsed]) 单次尝试处理。
        if isinstance(item, list) and item and all(
                isinstance(x, (dict, list, tuple)) for x in item):
            items = item
        else:
            items = [item]
        for one in items:
            if isinstance(one, dict):
                trial = _trial_from_dict(one)
            elif isinstance(one, (list, tuple)) and len(one) in (2, 3):
                trial = _normalize_trial(one)
            else:
                trial = _make_trial(one)
            trial["role"] = trial["role"] or role
            role_trials.append(trial)
    if role_trials:
        for key, item in unparsed:
            if isinstance(item, dict):
                trial = _trial_from_dict(item)
                trial["payload"] = trial["payload"] or key
            else:
                trial = _make_trial(item, key)
            role_trials.append(trial)
        return [t for t in role_trials
                if t["body"] is not None or t["elapsed"] is not None], None

    # 以 payload 为键的响应映射
    if value and all(isinstance(k, str) for k in value):
        mapped = []
        for payload, body in value.items():
            trial = _make_trial(body, payload)
            trial["role"] = _payload_role(payload)
            mapped.append(trial)
        return mapped, None

    return [], _SKIP_UNSUPPORTED


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #

def _prepare_text(value):
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text.strip().lower())
    if len(text) > 20000:
        text = text[:20000]
    return text


_KGRAM = 16


def _hash_overlap(left, right):
    """大段文本的近似共同字符数，用于避免超长序列上 O(n^2) 的精确比对。"""
    if len(left) < _KGRAM or len(right) < _KGRAM:
        return 0
    from collections import Counter
    left_grams = Counter(left[i:i + _KGRAM] for i in range(len(left) - _KGRAM + 1))
    right_grams = Counter(right[i:i + _KGRAM] for i in range(len(right) - _KGRAM + 1))
    shared = sum(min(count, right_grams[gram]) for gram, count in left_grams.items())
    return shared * _KGRAM


def _diff_stats(left, right):
    """计算两份响应的差异字符数和整页相似度。

    先掐掉公共前后缀，只对中间不同的一段做精确比对；中间太长时退化为
    k-gram 近似。长页面里的局部差异因此不会被公共模板稀释，也不会退化成慢比对。
    """
    left = _prepare_text(left)
    right = _prepare_text(right)
    total = len(left) + len(right)
    if left == right:
        return 0, 1.0
    if total == 0:
        return 0, 1.0
    if not left or not right:
        return max(len(left), len(right)), 0.0

    shortest = min(len(left), len(right))
    prefix = 0
    while prefix < shortest and left[prefix] == right[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < shortest - prefix
           and left[len(left) - 1 - suffix] == right[len(right) - 1 - suffix]):
        suffix += 1

    mid_left = left[prefix:len(left) - suffix]
    mid_right = right[prefix:len(right) - suffix]
    if not mid_left or not mid_right:
        matched = 0
    elif max(len(mid_left), len(mid_right)) <= 2000:
        from difflib import SequenceMatcher
        matched = sum(block.size for block in
                      SequenceMatcher(None, mid_left, mid_right,
                                      autojunk=False).get_matching_blocks())
    else:
        matched = _hash_overlap(mid_left, mid_right)

    matched_total = prefix + suffix + matched
    matched_total = min(matched_total, shortest)
    changed = max(0, total - 2 * matched_total)
    similarity = (2.0 * matched_total / total) if total else 1.0
    return changed, min(1.0, similarity)


def _similarity(left, right):
    """整页相似度。长页面会被公共模板稀释，判定时还要配合 _changed_chars。"""
    return _diff_stats(left, right)[1]


def _changed_chars(left, right):
    """两份响应的差异字符数（两边计数）。

    按整页比例算会让"长页面里一小段结果区不同"的布尔盲注被稀释，
    这里用绝对差异字符数，能抓住局部但稳定的差异。
    """
    return _diff_stats(left, right)[0]


def _pick_baseline(trials):
    for trial in trials:
        if trial["role"] == "baseline" and trial["body"] is not None:
            return trial
    return None


def _payloads_of(trials):
    result = []
    for trial in trials:
        if trial["payload"] is not None and str(trial["payload"]) not in result:
            result.append(str(trial["payload"]))
    return result


def _finding_base(url, param, trials):
    return {
        "url": url,
        "param": param,
        "kind": None,
        "status": "confirmed",
        "dbms": None,
        "confidence": 0.0,
        "evidence": "",
        "evidence_detail": {},
        "payload": None,
        "payloads": _payloads_of(trials),
        "request": "",
        "request_sample": "",
        "method": "GET",
        "baseline": None,
        "abnormal": None,
        "normal_page": None,
        "abnormal_page": None,
        "steps": [],
        "attempts": len(trials),
        "alternatives": [],
        "false_positive": False,
        "reason": None,
    }


def _build_request(url, param, payload):
    separator = "&" if "?" in url else "?"
    encoded_param = quote(str(param), safe="")
    encoded_value = quote("" if payload is None else str(payload), safe="")
    return "%s%s%s=%s" % (url, separator, encoded_param, encoded_value)


def _finalize(finding, url, param, payload, steps, baseline_body, abnormal_body):
    finding["payload"] = payload
    finding["request"] = _build_request(url, param, payload)
    finding["request_sample"] = finding["request"]
    finding["baseline"] = baseline_body
    finding["abnormal"] = abnormal_body
    finding["normal_page"] = baseline_body
    finding["abnormal_page"] = abnormal_body
    finding["steps"] = steps
    return finding


def _error_candidate(url, param, trials, baseline_trial):
    baseline_body = baseline_trial["body"] if baseline_trial else None
    baseline_text = None if baseline_body is None else str(baseline_body).lower()
    best = None
    for trial in trials:
        if trial["body"] is None:
            continue
        if baseline_trial is not None and trial is baseline_trial:
            continue
        matches = match_error_signatures(trial["body"])
        if not matches:
            continue
        # 基线页面本身就有同一条报错时，这条特征说明页面在报错、不是注入引入的，
        # 但要逐条剔除而不是整条 trial 丢掉——同一次响应里可能还有基线没有的其它库特征。
        if baseline_text is not None:
            matches = [item for item in matches
                       if item["signature"] not in baseline_text]
        if not matches:
            continue
        match = matches[0]
        if best is None or match["confidence"] > best[0]["confidence"]:
            best = (match, trial, matches)
    if best is None:
        return None
    match, trial, matches = best
    payload = trial["payload"] if trial["payload"] is not None else ERROR_PAYLOADS[0]
    finding = _finding_base(url, param, trials)
    finding["kind"] = "error"
    finding["dbms"] = match["dbms"]
    finding["confidence"] = match["confidence"]
    finding["evidence"] = "%s 报错特征：%s" % (match["dbms"], match["signature"])
    finding["evidence_detail"] = {
        "type": "error",
        "dbms": match["dbms"],
        "signature": match["signature"],
        "confidence": match["confidence"],
        "matched_payload": None if payload is None else str(payload),
        "other_dbms": [
            {"dbms": item["dbms"], "signature": item["signature"],
             "confidence": item["confidence"]}
            for item in matches[1:]
        ],
    }
    finding["alternatives"] = [
        {"kind": "error", "dbms": item["dbms"], "confidence": item["confidence"],
         "evidence": "%s 报错特征：%s" % (item["dbms"], item["signature"])}
        for item in matches[1:]
    ]
    steps = [
        "请求基线：GET %s?%s=<正常值>" % (url, param),
        "向参数 %s 提交 payload：%s" % (param, payload),
        "响应出现 %s 报错特征：%s" % (match["dbms"], match["signature"]),
        "确认该报错特征在基线响应中不存在，可判定为报错型注入",
    ]
    return _finalize(finding, url, param, payload, steps, baseline_body, trial["body"])


def _collect_boolean_trials(trials):
    """收集所有可用的真/假条件响应（同一角色可能登记了多组 payload）。"""
    true_trials = [t for t in trials
                   if _infer_role(t) == "true" and t["body"] is not None]
    false_trials = [t for t in trials
                    if _infer_role(t) == "false" and t["body"] is not None]
    return true_trials, false_trials


def _score_boolean_pair(true_body, false_body, baseline_body, has_baseline):
    """给一组真/假响应打分，返回 (命中强度, 置信度, 明细)。不命中返回 None。"""
    true_similarity = _similarity(true_body, false_body)
    diff = 1.0 - true_similarity
    sim_true = _similarity(true_body, baseline_body)
    sim_false = _similarity(false_body, baseline_body)
    asymmetry = abs(sim_true - sim_false)
    changed = _changed_chars(true_body, false_body)
    # 整页比例会被公共模板稀释，再用绝对差异字符数衡量"偏向某一侧"的程度。
    changed_true = _changed_chars(true_body, baseline_body)
    changed_false = _changed_chars(false_body, baseline_body)
    changed_asymmetry = abs(changed_true - changed_false)

    localized = changed >= BOOL_MIN_CHANGED_CHARS
    localized_asym = changed_asymmetry >= BOOL_MIN_CHANGED_CHARS
    strong_ratio = diff >= BOOL_MIN_DIFF and asymmetry >= BOOL_MIN_ASYMMETRY
    strong_chars = localized and localized_asym
    weak_ratio = diff >= BOOL_WEAK_DIFF and asymmetry >= BOOL_WEAK_DIFF
    weak_chars = localized and changed_asymmetry > 0
    if has_baseline:
        strong = strong_ratio or strong_chars
        weak = weak_ratio or weak_chars
        signal = max(diff, min(0.5, changed / 200.0))
        asymmetry_signal = max(asymmetry, min(0.5, changed_asymmetry / 200.0))
        confidence = min(0.95, 0.62 + min(0.2, asymmetry_signal * 0.25)
                         + min(0.13, signal * 0.15))
    else:
        strong = diff >= BOOL_MIN_DIFF_NO_BASE or localized
        weak = diff >= BOOL_WEAK_DIFF
        signal = max(diff, min(0.5, changed / 200.0))
        confidence = min(0.72, 0.55 + min(0.15, signal * 0.2))

    if not strong and not weak:
        return None
    return {
        "strong": strong,
        "weak": weak,
        "confidence": confidence,
        "true_similarity": true_similarity,
        "diff": diff,
        "sim_true": sim_true,
        "sim_false": sim_false,
        "asymmetry": asymmetry,
        "changed": changed,
        "changed_asymmetry": changed_asymmetry,
        "abnormal_body": false_body if sim_false <= sim_true else true_body,
    }


def _boolean_candidate(url, param, trials, baseline_trial):
    true_trials, false_trials = _collect_boolean_trials(trials)

    # 明确给了"正常/异常页面"但没有成对 payload：只能算待确认的差异。
    abnormal_trial = None
    for trial in trials:
        if trial["role"] == "abnormal" and trial["body"] is not None:
            abnormal_trial = trial
            break

    if not true_trials or not false_trials:
        if baseline_trial is not None and abnormal_trial is not None:
            baseline_body = baseline_trial["body"]
            abnormal_body = abnormal_trial["body"]
            diff = 1.0 - _similarity(baseline_body, abnormal_body)
            if diff >= BOOL_MIN_DIFF_NO_BASE:
                finding = _finding_base(url, param, trials)
                finding["kind"] = "boolean"
                finding["confidence"] = min(0.7, 0.55 + diff * 0.15)
                finding["evidence"] = "正常页面与异常页面存在差异，但缺少真/假对照 payload"
                finding["evidence_detail"] = {
                    "type": "boolean",
                    "baseline_present": True,
                    "difference": round(diff, 3),
                    "paired_payloads": False,
                }
                finding["status"] = "pending"
                payload = abnormal_trial["payload"]
                steps = [
                    "请求基线：GET %s?%s=<正常值>" % (url, param),
                    "提交异常 payload：%s" % payload,
                    "正常页面与异常页面差异 %.0f%%，缺少真/假对照，需人工确认" % (diff * 100),
                ]
                _finalize(finding, url, param, payload, steps,
                          baseline_body, abnormal_body)
                return finding, "pending"
        return None

    baseline_body = baseline_trial["body"] if baseline_trial else true_trials[0]["body"]
    has_baseline = baseline_trial is not None

    # 真/假 payload 可能登记了多组：逐组打分，取最强的一组。
    # 只看第一组会在第一组恰好无差异、后一组真有差异时漏报。
    best = None
    for true_trial in true_trials:
        for false_trial in false_trials:
            scored = _score_boolean_pair(true_trial["body"], false_trial["body"],
                                         baseline_body, has_baseline)
            if scored is None:
                continue
            rank = (scored["strong"], scored["confidence"])
            if best is None or rank > best[0]:
                best = (rank, scored, true_trial, false_trial)
    if best is None:
        return None

    _, scored, true_trial, false_trial = best
    true_body = true_trial["body"]
    false_body = false_trial["body"]
    true_similarity = scored["true_similarity"]
    diff = scored["diff"]
    sim_true = scored["sim_true"]
    sim_false = scored["sim_false"]
    asymmetry = scored["asymmetry"]
    changed = scored["changed"]
    changed_asymmetry = scored["changed_asymmetry"]
    strong = scored["strong"]
    confidence = scored["confidence"]
    abnormal_body = scored["abnormal_body"]
    finding = _finding_base(url, param, trials)
    finding["kind"] = "boolean"
    finding["confidence"] = round(confidence if strong else min(0.5, confidence), 3)
    finding["status"] = "confirmed" if strong else "pending"
    finding["evidence"] = (
        "布尔盲注：真条件响应与基线相似度 %.2f，假条件相似度 %.2f，"
        "真/假响应差异 %.2f（差异 %d 字符，偏向一侧 %d 字符）"
        % (sim_true, sim_false, diff, changed, changed_asymmetry)
    )
    finding["evidence_detail"] = {
        "type": "boolean",
        "baseline_present": has_baseline,
        "true_similarity": round(sim_true, 3),
        "false_similarity": round(sim_false, 3),
        "true_false_similarity": round(true_similarity, 3),
        "difference": round(diff, 3),
        "changed_chars": changed,
        "changed_asymmetry": changed_asymmetry,
        "asymmetry": round(asymmetry, 3),
        "true_payload": true_trial["payload"],
        "false_payload": false_trial["payload"],
    }
    payload = false_trial["payload"]
    steps = [
        "请求基线：GET %s?%s=<正常值>" % (url, param),
        "提交真条件 payload：%s" % true_trial["payload"],
        "提交假条件 payload：%s" % false_trial["payload"],
        "比较两次响应与基线的差异：真条件相似度 %.2f，假条件相似度 %.2f"
        % (sim_true, sim_false),
    ]
    if finding["status"] == "pending":
        steps.append("差异未达到确认阈值，标记为待确认")
    _finalize(finding, url, param, payload, steps, baseline_body, abnormal_body)
    return finding, finding["status"]


def _time_candidate(url, param, trials):
    elapsed_trials = [t for t in trials if t["elapsed"] is not None]
    if not elapsed_trials:
        return None

    baseline_trial = None
    for trial in trials:
        if trial["role"] == "baseline" and trial["elapsed"] is not None:
            baseline_trial = trial
            break

    explicit = []
    for trial in elapsed_trials:
        if _infer_role(trial) == "timed":
            explicit.append(trial)

    # 先定"慢样本"，再算基线：基线绝不能把慢样本自己算进去，
    # 否则中位数被抬高，差值/倍数偏小，本来该确认的会降成待确认。
    timed = explicit
    if not timed:
        slowest = max(elapsed_trials, key=lambda t: t["elapsed"])
        if baseline_trial is not None:
            compare = baseline_trial["elapsed"]
        else:
            others = [t["elapsed"] for t in elapsed_trials if t is not slowest]
            compare = statistics.median(others) if others else None
        if compare is not None:
            if (slowest["elapsed"] - compare >= TIME_MIN_DELTA
                    and slowest["elapsed"] / max(compare, 0.05) >= TIME_MIN_RATIO):
                timed = [slowest]
        elif slowest["elapsed"] >= TIME_ABSOLUTE:
            timed = [slowest]

    if not timed:
        return None

    observed = max(t["elapsed"] for t in timed)
    slow_trial = max(timed, key=lambda t: t["elapsed"])

    # 基线取值：显式 baseline 优先；否则用"除慢样本以外"的样本中位数。
    baseline_seconds = None
    if baseline_trial is not None:
        baseline_seconds = baseline_trial["elapsed"]
    else:
        baseline_values = [t["elapsed"] for t in elapsed_trials
                           if t is not slow_trial and t not in timed]
        if baseline_values:
            baseline_seconds = statistics.median(baseline_values)
    has_baseline = baseline_seconds is not None

    if has_baseline:
        delta = observed - baseline_seconds
        ratio = observed / max(baseline_seconds, 0.05)
        strong = delta >= TIME_MIN_DELTA and ratio >= TIME_MIN_RATIO
        weak = delta >= TIME_WEAK_DELTA and ratio >= TIME_WEAK_RATIO
    else:
        delta = observed
        ratio = None
        strong = observed >= TIME_ABSOLUTE
        weak = observed >= TIME_WEAK_DELTA * 2

    if not strong and not weak:
        return None

    if has_baseline:
        confidence = min(0.97, 0.6 + min(0.2, delta * 0.04)
                         + min(0.1, max(0.0, ratio - TIME_MIN_RATIO) * 0.02))
    else:
        confidence = min(0.68, 0.55 + min(0.12, observed * 0.02))
    if len(timed) >= 2:
        confidence = min(0.98, confidence + 0.04)

    # 正常页面优先取显式 baseline；没有时用非慢样本里最像"普通响应"的一条。
    baseline_body = None
    if baseline_trial is not None:
        baseline_body = baseline_trial["body"]
    else:
        for trial in elapsed_trials:
            if trial is not slow_trial and _infer_role(trial) != "timed":
                baseline_body = trial["body"]
                break

    finding = _finding_base(url, param, trials)
    finding["kind"] = "time"
    finding["confidence"] = round(confidence if strong else min(0.5, confidence), 3)
    finding["status"] = "confirmed" if strong else "pending"
    if has_baseline:
        finding["evidence"] = (
            "时间盲注：异常 payload 耗时 %.2fs，基线 %.2fs，差值 %.2fs（%.1f 倍）"
            % (observed, baseline_seconds, delta, ratio)
        )
    else:
        finding["evidence"] = (
            "时间盲注：异常 payload 耗时 %.2fs，缺少基线耗时，绝对值偏高"
            % observed
        )
    finding["evidence_detail"] = {
        "type": "time",
        "baseline_present": has_baseline,
        "baseline_seconds": (round(baseline_seconds, 3)
                             if has_baseline else None),
        "observed_seconds": round(observed, 3),
        "delta_seconds": round(delta, 3),
        "ratio": None if ratio is None else round(ratio, 3),
        "timed_payloads": _payloads_of(timed),
    }
    payload = slow_trial["payload"]
    steps = [
        ("请求基线：GET %s?%s=<正常值>，记录耗时 %.2fs" % (url, param, baseline_seconds)
         if has_baseline else "未提供基线耗时，改用绝对耗时 %.2fs 判断" % observed),
        "向参数 %s 提交 payload：%s" % (param, payload),
        ("记录响应耗时 %.2fs，比基线多 %.2fs" % (observed, delta)
         if has_baseline else "记录响应耗时 %.2fs" % observed),
        "重复提交确认耗时稳定复现" if finding["status"] == "confirmed"
        else "耗时差异未达到确认阈值，标记为待确认",
    ]
    _finalize(finding, url, param, payload, steps, baseline_body, slow_trial["body"])
    return finding, finding["status"]


def _pending_candidate(url, param, trials, baseline_trial):
    baseline_body = baseline_trial["body"] if baseline_trial else None
    for trial in trials:
        if trial["body"] is None or (baseline_trial is not None and trial is baseline_trial):
            continue
        signature = generic_error_signature(trial["body"])
        if signature is None:
            continue
        if baseline_body is not None:
            if signature in str(baseline_body).lower():
                continue
            if _similarity(baseline_body, trial["body"]) >= 0.99:
                continue
        payload = trial["payload"] if trial["payload"] is not None else ERROR_PAYLOADS[0]
        finding = _finding_base(url, param, trials)
        finding["kind"] = "pending"
        finding["status"] = "pending"
        finding["confidence"] = 0.45
        finding["evidence"] = "通用报错特征：%s（未匹配到具体数据库，待确认）" % signature
        finding["evidence_detail"] = {
            "type": "generic_error",
            "signature": signature,
            "dbms": None,
        }
        steps = [
            "请求基线：GET %s?%s=<正常值>" % (url, param),
            "向参数 %s 提交 payload：%s" % (param, payload),
            "响应出现通用报错特征：%s，未匹配到具体数据库" % signature,
            "标记为待确认，需人工复核或用数据库特征 payload 复测",
        ]
        return _finalize(finding, url, param, payload, steps,
                         baseline_body, trial["body"])
    return None


def _evaluate(url, param, trials):
    baseline_trial = _pick_baseline(trials)
    candidates = []

    error = _error_candidate(url, param, trials, baseline_trial)
    if error is not None:
        candidates.append(error)

    boolean = _boolean_candidate(url, param, trials, baseline_trial)
    if boolean is not None:
        candidates.append(boolean[0])

    time_finding = _time_candidate(url, param, trials)
    if time_finding is not None:
        candidates.append(time_finding[0])

    pending = _pending_candidate(url, param, trials, baseline_trial)
    if pending is not None:
        candidates.append(pending)

    if not candidates:
        return None

    priority = {"error": 3, "boolean": 2, "time": 1, "pending": 0}
    # 同一条参数在两种结论之间摇摆时，取证据最强的一条：
    # 先看是否已确认（confirmed 高于 pending），再比置信度，最后按类型优先级。
    winner = max(candidates, key=lambda item: (
        item["status"] == "confirmed",
        item["confidence"],
        priority.get(item["kind"], 0),
    ))
    alternatives = [
        {
            "kind": item["kind"],
            "confidence": item["confidence"],
            "status": item["status"],
            "evidence": item["evidence"],
            "dbms": item.get("dbms"),
        }
        for item in candidates
        if item is not winner
    ]
    alternatives.sort(key=lambda item: (-item["confidence"],
                                        item["kind"] != "error"))
    # winner["alternatives"] 可能已带同类型候选（例如 error 的其它数据库特征），
    # 合并后统一重排，保证列表始终按置信度降序。
    winner["alternatives"] = sorted(
        winner["alternatives"] + alternatives,
        key=lambda item: -item["confidence"])
    return winner


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #

def load(url, responses):
    """登记接口和每个参数的观测数据。

    ``responses`` 的值可以是响应正文、trial 字典、trial 列表，或按
    baseline/true/false/timed 命名的响应字典。重复登记同一接口会重置结论。
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url 不能为空")
    if not isinstance(responses, dict):
        raise ValueError("responses 必须是字典")
    # 深拷贝，避免调用方后续修改嵌套结构污染已登记的观测。
    _TARGETS[url] = copy.deepcopy(dict(responses))
    _RESULTS.pop(url, None)
    for key in [key for key in _FALSE_POSITIVES if key[0] == url]:
        _FALSE_POSITIVES.pop(key, None)
    return {"url": url, "params": len(responses)}


def check(url, params):
    """按参数和响应差异判定，每个参数只给一条结论。"""
    table = _TARGETS.get(url)
    if table is None:
        raise ValueError("接口未载入")
    if isinstance(params, (set, frozenset)):
        params = sorted(params)
    if not isinstance(params, (list, tuple)) or not params:
        raise ValueError("参数列表为空")

    requested = []
    for param in params:
        if not isinstance(param, str) or not param:
            raise ValueError("参数名不能为空")
        if param not in requested:
            requested.append(param)

    result = _RESULTS.setdefault(url, {
        "findings": {},
        "uncovered": {},
        "covered": set(),
        "requested": set(),
        "attempts_by_param": {},
    })

    call_findings = []
    call_uncovered = []
    for param in requested:
        result.setdefault("requested", set()).add(param)
        observation = table[param] if param in table else _MISSING
        trials, skip = _normalize_observation(observation)
        # 重复 check 同一参数只保留本轮尝试数，避免 attempts 被反复累加。
        result.setdefault("attempts_by_param", {})[param] = len(trials)

        if not trials:
            reason = skip or _SKIP_UNSUPPORTED
            entry = {"param": param, "reason": reason,
                     "reason_code": _SKIP_CODES[reason]}
            result["uncovered"][param] = entry
            result["covered"].discard(param)
            result["findings"].pop(param, None)
            call_uncovered.append(dict(entry))
            continue

        result["covered"].add(param)
        result["uncovered"].pop(param, None)
        finding = _evaluate(url, param, trials)
        if finding is None:
            result["findings"].pop(param, None)
            continue
        if (url, param) in _FALSE_POSITIVES:
            finding["false_positive"] = True
            finding["status"] = "false_positive"
            finding["reason"] = _FALSE_POSITIVES[(url, param)]
        result["findings"][param] = finding
        call_findings.append(copy.deepcopy(finding))

    call_findings.sort(key=lambda item: -item["confidence"])
    # 未检测的是"登记了但从未进入过 check、也不在跳过清单里"的参数，
    # 与 report() 口径一致；连续多次 check 不会把已覆盖的参数算进来。
    flagged = {entry["param"] for entry in result["uncovered"].values()}
    not_requested = [key for key in table
                     if key not in result.get("requested", set()) and key not in flagged]
    pending = [item["param"] for item in call_findings if item["status"] == "pending"]
    return {
        "url": url,
        "findings": call_findings,
        "covered": [param for param in requested if param in result["covered"]],
        "uncovered": call_uncovered,
        "skipped": copy.deepcopy(call_uncovered),
        "pending": pending,
        "not_requested": not_requested,
        "duplicates": 0,
    }


def _interface_report(url):
    result = _RESULTS.get(url, {"findings": {}, "uncovered": {}, "covered": set(),
                                "requested": set(), "attempts_by_param": {}})
    requested = set(result.get("requested", set()))
    findings = sorted(result["findings"].values(), key=lambda item: -item["confidence"])
    pending = [item["param"] for item in findings if item["status"] == "pending"]
    false_positives = [
        {"param": item["param"], "url": url, "reason": item["reason"]}
        for item in findings if item["status"] == "false_positive"
    ]
    # 未覆盖只收录"检测过但没测成"的参数，原因固定为超时/拒绝/协议不支持。
    # 登记了但从未进入 check 的参数单独放进 not_requested，不混用跳过原因。
    unavailable = [dict(entry) for entry in result["uncovered"].values()]
    flagged = {entry["param"] for entry in unavailable}
    not_requested = [param for param in _TARGETS.get(url, {})
                     if param not in requested and param not in flagged]
    available = sorted(requested - flagged)
    return {
        "url": url,
        "findings": len(findings),
        "confirmed": sum(1 for item in findings if item["status"] == "confirmed"),
        "pending": len(pending),
        "pending_params": pending,
        "false_positives": len(false_positives),
        "false_positive_details": false_positives,
        "covered": available,
        "uncovered": unavailable,
        "uncovered_count": len(unavailable),
        "skipped": len(unavailable),
        "not_requested": not_requested,
        "duplicates": 0,
        "attempts": sum(result.get("attempts_by_param", {}).values()),
        "conclusions": copy.deepcopy(findings),
    }


def report(url=None):
    """按接口汇总结论；传 url 只看一个接口，否则给全部接口。"""
    if url is not None:
        if url not in _TARGETS and url not in _RESULTS:
            raise ValueError("接口未载入")
        block = _interface_report(url)
        block["params"] = {item["param"]: 1 for item in block["conclusions"]}
        return block

    interfaces = {}
    ordered = list(_TARGETS.keys())
    for url in _RESULTS:
        if url not in ordered:
            ordered.append(url)
    for url in ordered:
        interfaces[url] = _interface_report(url)

    conclusions = []
    params = {}
    for block in interfaces.values():
        for item in block["conclusions"]:
            conclusions.append(item)
            params[item["param"]] = params.get(item["param"], 0) + 1

    uncovered = []
    for block_url, block in interfaces.items():
        for item in block["uncovered"]:
            uncovered.append(dict(item, url=block_url))

    # 全局与单接口保持同一套字段：uncovered 一律是列表，
    # 数量用 uncovered_count/skipped 表示，避免同名键在两种形态间切换。
    return {
        "findings": len(conclusions),
        "confirmed": sum(1 for item in conclusions if item["status"] == "confirmed"),
        "params": params,
        "duplicates": 0,
        "pending": sum(1 for item in conclusions if item["status"] == "pending"),
        "pending_params": [item["param"] for item in conclusions
                           if item["status"] == "pending"],
        "false_positives": sum(1 for item in conclusions
                               if item["status"] == "false_positive"),
        "false_positive_details": [
            {"param": item["param"], "url": item["url"], "reason": item["reason"]}
            for item in conclusions if item["status"] == "false_positive"
        ],
        "skipped": len(uncovered),
        "uncovered": copy.deepcopy(uncovered),
        "uncovered_count": len(uncovered),
        "uncovered_params": copy.deepcopy(uncovered),
        "not_requested": [param for block_url, block in interfaces.items()
                          for param in block["not_requested"]],
        "attempts": sum(block["attempts"] for block in interfaces.values()),
        "conclusions": copy.deepcopy(conclusions),
        "interfaces": interfaces,
    }


_UNSET = object()


def mark_false_positive(url, param=None, reason=_UNSET):
    """把一条结论标为误报并记录原因。

    支持 ``mark_false_positive(url, param, reason)``，也支持只给
    ``(param, reason)`` 时在唯一命中的接口上打标。
    """
    # 只给两个位置参数时视为 (param, reason)；显式传了第三参即使为 None，
    # 也按 (url, param, reason) 处理，交给下面报"原因不能为空"。
    if reason is _UNSET and param is not None:
        reason = param
        param = url
        url = None
    elif reason is _UNSET:
        reason = None

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("误报原因不能为空")
    if not isinstance(param, str) or not param:
        raise ValueError("参数名不能为空")

    if url is None:
        matches = [
            (known, item["param"])
            for known, block in _RESULTS.items()
            for item in block["findings"].values()
            if item["param"] == param
        ]
        if not matches:
            raise ValueError("未找到该参数的结论")
        if len(matches) > 1:
            raise ValueError("参数存在于多个接口，请指定 url")
        url, param = matches[0]
    else:
        block = _RESULTS.get(url)
        if block is None or param not in block["findings"]:
            raise ValueError("未找到该参数的结论")

    reason = reason.strip()
    _FALSE_POSITIVES[(url, param)] = reason
    finding = _RESULTS[url]["findings"][param]
    finding["false_positive"] = True
    finding["status"] = "false_positive"
    finding["reason"] = reason
    return copy.deepcopy(finding)


def reset():
    """清空所有接口、结论和误报标记，开始新一轮检测。"""
    _TARGETS.clear()
    _RESULTS.clear()
    _FALSE_POSITIVES.clear()
    return {"cleared": True}


# 常用别名，方便运营脚本调用。
mark = mark_false_positive
flag_false_positive = mark_false_positive
