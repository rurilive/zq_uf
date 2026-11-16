"""服务版本识别：响应头、页面特征、默认文件、协议行为多信号综合。

identify(host, port) 对每个端口独立识别，同一服务的多处入口分别记录；
report() 按主机分端口汇总，不合并版本。识别不出的标为 unknown 并保留
原始响应片段，按主机汇总便于补采；人工可用 override() 推翻结论。
"""

import re

_RESPONSES = {}
_OVERRIDES = {}

_RAW_LIMIT = 500
_CONTEXT = 60

_CONFIDENCE_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "manual": 4}

# 识别规则：signal 为取特征的位置，pattern 第一个捕获组是版本（可没有）。
_RULES = [
    # 响应头
    {"signal": "server", "pattern": r"nginx/([\d.]+)", "service": "nginx", "confidence": "high"},
    {"signal": "server", "pattern": r"Apache/([\d.]+)", "service": "apache", "confidence": "high"},
    {"signal": "server", "pattern": r"Microsoft-IIS/([\d.]+)", "service": "iis", "confidence": "high"},
    {"signal": "server", "pattern": r"lighttpd/([\d.]+)", "service": "lighttpd", "confidence": "high"},
    {"signal": "server", "pattern": r"openresty/([\d.]+)", "service": "openresty", "confidence": "high"},
    {"signal": "powered-by", "pattern": r"PHP/([\d.]+)", "service": "php", "confidence": "medium"},
    {"signal": "powered-by", "pattern": r"Express", "service": "express", "confidence": "medium"},
    {"signal": "powered-by", "pattern": r"ASP\.NET", "service": "aspnet", "confidence": "medium"},
    # 页面特征与默认文件（默认页内容，头被改过时兜底）
    {"signal": "body", "pattern": r"Welcome to nginx!", "service": "nginx", "confidence": "medium"},
    {"signal": "body", "pattern": r"Apache2 \w+ Default Page", "service": "apache", "confidence": "medium"},
    {"signal": "body", "pattern": r"IIS Windows Server", "service": "iis", "confidence": "medium"},
    {"signal": "body", "pattern": r"Apache Tomcat/([\d.]+)", "service": "tomcat", "confidence": "medium"},
    {"signal": "title", "pattern": r"phpMyAdmin", "service": "phpmyadmin", "confidence": "low"},
    # 协议行为（非 HTTP 的欢迎横幅）
    {"signal": "banner", "pattern": r"SSH-[\d.]+-OpenSSH_([\w.]+)", "service": "openssh", "confidence": "high"},
    {"signal": "banner", "pattern": r"220.*vsFTPd ([\d.]+)", "service": "vsftpd", "confidence": "high"},
    {"signal": "banner", "pattern": r"220.*FileZilla Server[^\r\n]*?([\d.]+)", "service": "filezilla", "confidence": "high"},
    {"signal": "banner", "pattern": r"220 .*ESMTP Postfix", "service": "postfix", "confidence": "high"},
]


def register(host, port, response):
    """登记一次原始响应，供识别时取用。"""
    if not isinstance(response, str):
        raise ValueError("response must be a string")
    _RESPONSES[(host, int(port))] = response
    return {"host": host, "port": int(port), "stored": True}


def override(host, port, service, version, note=""):
    """人工推翻自动结论，记下实际服务与版本。"""
    _OVERRIDES[(host, int(port))] = {"service": service, "version": version, "note": note}
    return {"host": host, "port": int(port), "service": service, "version": version, "overridden": True}


def _split_response(response):
    if "\r\n\r\n" in response:
        return response.split("\r\n\r\n", 1)
    if "\n\n" in response:
        return response.split("\n\n", 1)
    return response, ""


def _signals(response):
    """从原始响应里抽出各识别信号。"""
    head, body = _split_response(response)
    lines = head.splitlines()
    first_line = lines[0] if lines else ""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    if m:
        title = m.group(1).strip()
    is_http = first_line.upper().startswith("HTTP/")
    return {
        "server": headers.get("server", ""),
        "powered-by": headers.get("x-powered-by", ""),
        "title": title,
        "body": body,
        "banner": "" if is_http else first_line,
    }


def _snippet(text, match):
    start = max(0, match.start() - 10)
    end = min(len(text), match.end() + _CONTEXT)
    return text[start:end].strip()


def _match_rules(signals):
    evidence = []
    for rule in _RULES:
        text = signals.get(rule["signal"], "")
        if not text:
            continue
        m = re.search(rule["pattern"], text, re.I)
        if not m:
            continue
        evidence.append({
            "signal": rule["signal"],
            "service": rule["service"],
            "version": m.group(1) if m.groups() else None,
            "confidence": rule["confidence"],
            "snippet": _snippet(text, m),
        })
    return evidence


def _base(host, port):
    return {
        "host": host,
        "port": int(port),
        "service": "unknown",
        "version": None,
        "confidence": "none",
        "evidence": [],
        "raw": "",
        "unknown": True,
    }


def identify(host, port):
    """按服务和端口分别识别，综合响应头、页面特征、默认文件、协议行为。"""
    port = int(port)
    result = _base(host, port)
    response = _RESPONSES.get((host, port))
    if response is not None:
        result["raw"] = response[:_RAW_LIMIT]

    manual = _OVERRIDES.get((host, port))
    if manual is not None:
        result.update({
            "service": manual["service"],
            "version": manual["version"],
            "confidence": "manual",
            "evidence": [{
                "signal": "manual",
                "service": manual["service"],
                "version": manual["version"],
                "confidence": "manual",
                "snippet": manual["note"],
            }],
            "unknown": False,
        })
        return result

    if response is None:
        return result

    signals = _signals(response)
    evidence = _match_rules(signals)
    # 看到但认不出的 Server 头（可能被改过）也留进依据，供人工核对。
    if signals["server"] and not any(e["signal"] == "server" for e in evidence):
        evidence.append({
            "signal": "server",
            "service": None,
            "version": None,
            "confidence": "none",
            "snippet": "Server: " + signals["server"],
        })
    result["evidence"] = evidence

    identified = [e for e in evidence if e["service"]]
    if not identified:
        return result

    best = max(identified, key=lambda e: _CONFIDENCE_RANK[e["confidence"]])
    version = best["version"]
    if version is None:
        for e in identified:
            if e["service"] == best["service"] and e["version"]:
                version = e["version"]
                break
    result.update({
        "service": best["service"],
        "version": version,
        "confidence": best["confidence"],
        "unknown": False,
    })
    return result


def report():
    """按主机分端口列出结论，不合并版本；未知项按主机汇总便于补采。"""
    hosts = {}
    unknown = {}
    distribution = {}
    for host, port in sorted(_RESPONSES):
        item = identify(host, port)
        hosts.setdefault(host, []).append({
            "port": port,
            "service": item["service"],
            "version": item["version"],
            "confidence": item["confidence"],
        })
        if item["unknown"]:
            unknown.setdefault(host, []).append(port)
        else:
            versions = distribution.setdefault(item["service"], {})
            key = item["version"] or "unknown"
            versions[key] = versions.get(key, 0) + 1
    return {
        "hosts": hosts,
        "ports": len(_RESPONSES),
        "unknown": unknown,
        "distribution": distribution,
    }
