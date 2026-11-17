"""按回显位置判断的反射型 / 存储型 XSS 检测。

设计原则：
1. 用唯一 canary 标记回显，而不是整段 payload 做原文匹配；
2. 每处回显先定位上下文（HTML 正文 / 属性 / URL / 脚本 / 注释 / 标签名）；
3. 按上下文先还原编码，再判断能不能跳出上下文执行；
4. 同一参数在多处回显逐处判断；同上下文、同结论的重复回显合并为一条；
5. 拿不准的给 manual，不武断报漏洞也不武断放过。
"""

import re

from .context import context_at, tokenize
from .decode import (
    decode_position_map,
    has_raw_quote,
    has_raw_tag_close,
    restore,
)

# 唯一标记：字母数字，自身不含任何需要编码的特殊字符
CANARY = "zxs9f4k7q2"
# 对外保留的经典 payload（旧入口兼容）
PAYLOAD = "<script>alert(1)</script>"

_PAGES = {}
_ENTRIES = {}
_CHECKS = {}
_ENTRY_SEQ = [0]

_VERDICT_VULN = "vulnerable"
_VERDICT_SAFE = "safe"
_VERDICT_MANUAL = "manual"


# ---------------------------------------------------------------- 登记入口

def load(page, body, content_type="text/html"):
    """登记页面正文。"""
    if not isinstance(page, str) or not page:
        raise ValueError("page 不能为空")
    if not isinstance(body, str):
        raise ValueError("body 必须是字符串")
    _PAGES[page] = {"body": body, "content_type": content_type}
    return {"page": page, "size": len(body), "content_type": content_type}


def submit(page, param, payload, entry_id=None):
    """登记存储型注入入口（提交点）。

    存储型没有自动发现入口，由人工/爬虫登记提交动作，检测时再到
    回显页面上找这条记录携带的 canary。payload 不含 canary 时自动包裹。
    """
    if page not in _PAGES:
        raise ValueError("页面未载入: %s" % page)
    if not isinstance(param, str) or not param:
        raise ValueError("参数名不能为空")
    if not isinstance(payload, str) or not payload:
        raise ValueError("payload 不能为空")
    if entry_id is None:
        _ENTRY_SEQ[0] += 1
        entry_id = "entry-%d" % _ENTRY_SEQ[0]
    if CANARY not in payload:
        payload = CANARY + payload
    _ENTRIES[entry_id] = {
        "entry_id": entry_id,
        "page": page,
        "param": param,
        "payload": payload,
        "status": "unobserved",
    }
    return dict(_ENTRIES[entry_id])


# ---------------------------------------------------------------- canary 定位

def _all_canary_offsets(body):
    """找出 canary 的全部回显，返回原文偏移列表。

    canary 自身是字母数字，无论 payload 是否编码都以原文出现；编码形态
    在拿到回显窗口后根据 canary 之后的注入内容另行判定，这里只负责定位。
    实体/百分号解码后可能出现新的 canary 位置（编码字符落在 canary 前），
    通过位置映射换算回原文偏移后一并收集、去重。
    """
    found = set()
    start = 0
    while True:
        idx = body.find(CANARY, start)
        if idx < 0:
            break
        found.add(idx)
        start = idx + 1

    for kind in ("percent", "entity"):
        decoded, mapping = decode_position_map(body, kind)
        if mapping is None:
            continue
        start = 0
        while True:
            idx = decoded.find(CANARY, start)
            if idx < 0:
                break
            found.add(mapping[idx])
            start = idx + 1

    return sorted(found)


def _detect_encoding(window):
    """根据 canary 之后的注入内容判断回显采用了哪种编码。"""
    idx = window.find(CANARY)
    injected = window[idx + len(CANARY):] if idx >= 0 else window
    if re.search(r"&#x?[0-9a-fA-F]+;|&[a-zA-Z]+;", injected):
        return "entity"
    if re.search(r"%[0-9a-fA-F]{2}", injected):
        return "percent"
    return "raw"


# ---------------------------------------------------------------- 回显窗口

def _echo_window(body, ctx, offset, regions):
    """返回 (分析片段, 展示片段)，片段均从 canary 起点开始。

    关键：分析片段必须排除上下文自身的定界符，但保留注入内容里的定界符。
    - 属性（含 URL 属性）：取到标签结束，注入引号/空白都在其中；
    - 脚本：canary 注入延续到区域边界时，纳入注入带出的 </script>；
    - 注释：保留区域定界符，由判定逻辑区分注入还是页面自有；
    - 正文：canary 后无实体文本时，附带紧跟的一个标签（注入的裸标签）。
    """
    context = ctx["context"]
    region = ctx["region"]
    inject_start = offset + len(CANARY)
    if context in ("attribute", "url"):
        # 只取本属性值区间：不能延伸到标签结束，否则会把同级的其他属性
        # （如 onerror=...）误当成注入内容。
        hit = None
        for values in region.get("attrs", {}).values():
            for vs, ve, quote in values:
                if vs <= offset < ve:
                    hit = (vs, ve, quote)
                    break
            if hit:
                break
        if hit:
            _vs, ve, quote = hit
            if quote:
                # 带引号：值区间在闭合引号前结束。把该闭合引号纳入片段，
                # 由判定逻辑判断它是注入闭合点还是页面结构定界符
                end = min(ve + 1, region["end"])
                # 注入的引号会让标签提前闭合：标签结束后还紧跟另一个元素
                ctx["_tag_terminated_early"] = (
                    region["end"] < len(body) and body[region["end"]] == "<")
            else:
                # 无引号：值区间在空白/ > 前结束，延伸到标签结束以纳入断开字符
                end = region["end"]
        else:
            end = region["end"]
    elif context == "script":
        # 脚本区域在 </script> 处结束。canary 注入内容若一直延续到区域
        # 边界（中间没有别的脚本代码），说明这个 </script> 是注入带进来的，
        # 把它纳入片段；否则是页面自有的闭合标签，切片停在区域边界
        end = region["end"]
        if not body[inject_start:end].strip() and body[end:end + 1] == "<":
            close = body.find(">", end)
            end = close + 1 if close >= 0 else region["end"]
    elif context == "comment":
        # 注释区域在第一个 --> 处结束，区域末尾三个字符就是该定界符。
        # 切片保留它，由判定逻辑结合 canary 位置区分是注入还是页面自有
        end = region["end"]
    elif context == "html":
        # 正文区域边界在相邻标签前。区域内 canary 后无文本说明紧跟一个
        # 标签（注入的裸标签会被解析为真正元素），附带这一个标签；
        # 区域内还有 &lt; 等实体文本则属编码回显，不扩展到页面自有标签
        end = region["end"]
        text_after = body[inject_start:end]
        if not text_after.strip() and end < len(body) and body[end] == "<":
            close = body.find(">", end)
            end = close + 1 if close >= 0 else len(body)
    else:
        end = region["end"] if region else len(body)
    full = body[offset:max(offset, end)]
    snippet = full if len(full) <= 200 else full[:200] + "…"
    return full, snippet


# ---------------------------------------------------------------- 上下文判定

def _split_canary(text):
    """把片段拆成 (canary, 注入部分)；找不到 canary 时整段算注入。"""
    idx = text.find(CANARY)
    if idx < 0:
        return "", text
    return text[:idx + len(CANARY)], text[idx + len(CANARY):]


def _verdict_html(raw_inject, restored_inject, encoding="raw"):
    # 浏览器实体解码后出现尖括号才有杀伤力；原文就有裸尖括号说明未编码
    if "<" in raw_inject or ">" in raw_inject:
        return _VERDICT_VULN, "裸 < 或 > 未被实体编码，可注入标签"
    if "<" in restored_inject or ">" in restored_inject:
        # 还原后有尖括号，说明尖括号来自实体解码（含双重编码只解出文本实体），
        # 正文内只渲染为文本，不能形成标签
        return _VERDICT_SAFE, "尖括号以 HTML 实体编码，正文内只渲染为文本"
    if encoding == "percent":
        # 百分号编码只在 URL 上下文解码，HTML 正文里 %3C 就是普通文本
        return _VERDICT_SAFE, "百分号编码在 HTML 正文中不被解码，无法形成标签"
    if "&" in raw_inject:
        # 存在编码痕迹但还原后仍无尖括号，属于被编码的其他字符
        return _VERDICT_SAFE, "特殊字符经编码处理，正文内无法形成标签"
    return _VERDICT_MANUAL, "未见尖括号，无法确认是否存在注入"


def _verdict_attribute(raw_inject, restored_inject, quote, tag_terminated_early=False):
    if quote:
        first_quote = raw_inject.find(quote)
        if first_quote >= 0:
            before = raw_inject[:first_quote]
            # 引号让标签提前闭合、后面紧跟新元素，确认是注入的闭合点
            if tag_terminated_early and not before.strip():
                return _VERDICT_VULN, "裸引号 %s 可闭合属性值并提前结束标签" % quote
            # 引号前有实体编码内容，注入被编码包住，无法闭合
            if "&" in before:
                return _VERDICT_SAFE, "引号为属性结构，注入内容被实体编码"
            # 引号正常结束属性值，标签完整闭合，属页面结构定界符
            return _VERDICT_SAFE, "引号为属性值的结构定界符，无法跳出"
        if quote in restored_inject:
            return _VERDICT_SAFE, "引号被实体编码，属性值无法闭合"
        return _VERDICT_SAFE, "回显不含引号，属性值内无法跳出"
        return _VERDICT_SAFE, "回显不含引号，属性值内无法跳出"
    # 无引号属性：空白或 > 都能打断属性（这些字符一般不做实体编码）
    for ch in restored_inject:
        if ch in " \t\n\r\f\v":
            return _VERDICT_VULN, "无引号属性中的空白可断开属性"
        if ch == ">":
            return _VERDICT_VULN, "裸 > 可结束标签"
    return _VERDICT_SAFE, "回显不含空白或 >，无法打断无引号属性"


def _verdict_url(raw_inject, restored_inject):
    # 原文即裸 javascript: 才可执行；靠百分号/实体解码才拼出来的协议，
    # URL 解析阶段不会把解码结果重新当协议处理
    raw_head = raw_inject.lstrip().lower()
    if raw_head.startswith("javascript:") or raw_head.startswith("data:text/html"):
        return _VERDICT_VULN, "可执行协议原文出现在 URL 属性内"
    head = restored_inject.lstrip().lower()
    if head.startswith("javascript:") or head.startswith("data:text/html"):
        return _VERDICT_SAFE, "协议经编码后出现，URL 解析不会将其作为协议执行"
    return _VERDICT_SAFE, "非可执行协议，URL 属性内不能触发脚本"


def _verdict_script(raw_inject, string_info, tag):
    if tag == "style":
        return _VERDICT_MANUAL, "样式块内回显，CSS 注入需人工确认"
    # <script> 不做实体解码，</script> 必须原文出现才有效
    if has_raw_tag_close(raw_inject):
        return _VERDICT_VULN, "裸 </script> 可闭合脚本块"
    if string_info and string_info.get("in_string"):
        quote = string_info.get("quote", "'")
        first_quote = raw_inject.find(quote)
        if first_quote >= 0 and has_raw_quote(raw_inject, quote):
            # 引号若紧邻 canary（中间只有空白），是注入的闭合点；
            # 否则它是字符串自身的结构定界符，后面还有合法脚本代码
            before = raw_inject[:first_quote]
            if not before.strip():
                return _VERDICT_VULN, "裸引号可闭合脚本字符串后注入代码"
        return _VERDICT_SAFE, "字符串内引号被转义或为结构定界符，无法跳出"
    if string_info and not string_info.get("in_string"):
        # 回显在字符串外（代码区）：任何标记都按代码解析，无需闭合
        return _VERDICT_VULN, "回显落在脚本代码区，标记随代码解析"
    return _VERDICT_MANUAL, "脚本内位置无法确定，需人工确认"


def _judge(window, restored, ctx, encoding):
    context = ctx["context"]
    _raw_canary, raw_inject = _split_canary(window)
    _restored_canary, restored_inject = _split_canary(restored)
    if context == "html":
        return _verdict_html(raw_inject, restored_inject, encoding)
    if context == "attribute":
        return _verdict_attribute(raw_inject, restored_inject,
                                  ctx.get("quote", ""),
                                  ctx.get("_tag_terminated_early", False))
    if context == "url":
        return _verdict_url(raw_inject, restored_inject)
    if context == "script":
        return _verdict_script(raw_inject, ctx.get("string"),
                               ctx.get("tag", "script"))
    if context == "comment":
        # 区域末尾的 --> 是定界符。canary 之后到定界符之间若没有别的注释
        # 文本，说明该定界符是注入内容带进来的（页面自有定界符前必有内容）
        terminator = raw_inject.rfind("-->")
        if terminator < 0:
            terminator = raw_inject.rfind("--!>")
        if terminator >= 0 and not raw_inject[:terminator].strip():
            return _VERDICT_VULN, "裸注释结束符可跳出注释"
        return _VERDICT_SAFE, "注释内回显，注释结束符为页面自有或被编码"
    if context == "tagname":
        return _VERDICT_VULN, "回显落在标签名/标签结构内，可改写标记"
    return _VERDICT_MANUAL, ctx.get("reason", "上下文无法确定")


# ---------------------------------------------------------------- 主检测

def _scan_body(page, param, payload, source, entry_id):
    page_info = _PAGES[page]
    body = page_info["body"]
    if page_info["content_type"] != "text/html":
        return [], [{
            "offset": 0,
            "reason": "非 HTML 响应(%s)，不做页面解析" % page_info["content_type"],
        }]
    regions, parse_errors = tokenize(body)
    findings = []
    for offset in _all_canary_offsets(body):
        ctx = context_at(body, offset, regions)
        window, snippet = _echo_window(body, ctx, offset, regions)
        restored, chain = restore(window, ctx["context"])
        encoding = _detect_encoding(window)
        verdict, reason = _judge(window, restored, ctx, encoding)
        findings.append({
            "page": page,
            "param": param,
            "source": source,
            "entry_id": entry_id,
            "payload": payload,
            "offset": offset,
            "context": ctx["context"],
            "context_detail": {
                key: value for key, value in ctx.items() if key != "region"
            },
            "encoding": encoding,
            "echo": snippet,
            "restored": restored if len(restored) <= 200
            else restored[:200] + "…",
            "restore_chain": chain,
            "verdict": verdict,
            "reason": reason,
        })
    return findings, parse_errors


def _merge(findings):
    """同上下文、同结论、同编码、同来源的重复回显合并为一条。"""
    groups = {}
    order = []
    for item in findings:
        key = (item["context"], item["verdict"], item["encoding"],
               item["source"], item["entry_id"], item["param"])
        if key not in groups:
            groups[key] = dict(item)
            groups[key]["offsets"] = [item["offset"]]
            groups[key]["count"] = 1
            order.append(key)
        else:
            groups[key]["offsets"].append(item["offset"])
            groups[key]["count"] += 1
    return [groups[key] for key in order]


def check(url, param, payload=None, source="reflected", entry_id=None):
    """检测一个（或一组）参数在页面上的回显。

    url     已通过 load 登记的页面标识；
    param   参数名字符串，或参数名列表（列表逐参数给出未覆盖原因）；
    payload 实际提交的参数值，需携带 CANARY，缺省用经典 PAYLOAD；
    source  reflected / stored，存储型需同时给 entry_id。
    返回命中位置、上下文、payload、还原过程与未覆盖参数。
    """
    if url not in _PAGES:
        raise ValueError("页面未载入: %s" % url)
    if source not in ("reflected", "stored"):
        raise ValueError("source 只能是 reflected 或 stored")
    if source == "stored" and not entry_id:
        raise ValueError("存储型检测必须提供 entry_id")
    if payload is None:
        payload = CANARY + PAYLOAD
    if CANARY not in payload:
        payload = CANARY + payload

    params = [param] if isinstance(param, str) else list(param)
    if not params or any(not isinstance(name, str) or not name for name in params):
        raise ValueError("参数名不能为空")

    raw_findings, parse_errors = _scan_body(url, params[0], payload, source, entry_id)
    merged = _merge(raw_findings)

    observed_params = {item["param"] for item in merged}
    uncovered = [
        {"param": name, "reason": "响应中未找到该参数的 canary 回显"}
        for name in params if name not in observed_params
    ]
    # 合并同参数时只保留参数名维度的未覆盖列表
    if len(params) == 1 and not merged:
        uncovered = [{"param": params[0],
                      "reason": "响应中未找到该参数的 canary 回显"}]

    result = {
        "page": url,
        "params": params,
        "source": source,
        "entry_id": entry_id,
        "payload": payload,
        "findings": merged,
        "uncovered": uncovered,
        "parse_errors": [
            {"offset": err["offset"], "reason": err["reason"]}
            for err in parse_errors
        ],
    }
    _CHECKS[url] = result
    if source == "stored" and entry_id and entry_id in _ENTRIES:
        _ENTRIES[entry_id]["status"] = "observed" if merged else "unobserved"
    return dict(result)


# ---------------------------------------------------------------- 汇总

def report():
    """按页面汇总检测结论，供运营按页面查看。"""
    pages = {}
    total = {"vulnerable": 0, "manual": 0, "safe": 0}
    contexts = {}
    uncovered = []
    parse_errors = []

    for page, result in _CHECKS.items():
        page_block = pages.setdefault(page, {
            "verdict": "safe",
            "vulnerable": 0,
            "manual": 0,
            "safe": 0,
            "params": sorted(result["params"]),
        })
        for item in result["findings"]:
            total[item["verdict"]] += 1
            page_block[item["verdict"]] += 1
            contexts[item["context"]] = contexts.get(item["context"], 0) + 1
        if page_block["vulnerable"]:
            page_block["verdict"] = "vulnerable"
        elif page_block["manual"]:
            page_block["verdict"] = "manual"
        for item in result["uncovered"]:
            uncovered.append({"page": page, "param": item["param"],
                              "reason": item["reason"]})
        for err in result["parse_errors"]:
            parse_errors.append({"page": page, "offset": err["offset"],
                                 "reason": err["reason"]})

    pending_entries = [
        {"entry_id": entry["entry_id"], "page": entry["page"],
         "param": entry["param"], "reason": "提交后未在任何页面观察到回显"}
        for entry in _ENTRIES.values() if entry["status"] != "observed"
    ]
    return {
        "pages": pages,
        "hits": sum(total.values()),
        "verdict_counts": total,
        "contexts": contexts,
        "uncovered": uncovered,
        "uncovered_count": len(uncovered),
        "parse_errors": parse_errors,
        "parse_error_count": len(parse_errors),
        "stored_entries": {
            "total": len(_ENTRIES),
            "observed": sum(1 for e in _ENTRIES.values()
                            if e["status"] == "observed"),
            "pending": pending_entries,
        },
    }


def reset():
    """清空全部登记与检测结果（主要给测试用）。"""
    _PAGES.clear()
    _ENTRIES.clear()
    _CHECKS.clear()
    _ENTRY_SEQ[0] = 0
