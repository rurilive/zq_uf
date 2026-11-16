"""服务版本识别，当前只看 Server 响应头。"""

_RESPONSES = {}


def register(host, port, response):
    """登记一次原始响应，供识别时取用。"""
    if not isinstance(response, str):
        raise ValueError("response must be a string")
    _RESPONSES[(host, int(port))] = response
    return {"host": host, "port": int(port), "stored": True}


def _server_header(response):
    for line in response.splitlines():
        if line.lower().startswith("server:"):
            return line.split(":", 1)[1].strip()
    return ""


def identify(host, port):
    """只从 Server 头切出版本，没有头就当未知。"""
    response = _RESPONSES.get((host, int(port)))
    if response is None:
        return {
            "host": host,
            "port": int(port),
            "service": "unknown",
            "version": None,
            "evidence": "",
            "raw": "",
        }
    header = _server_header(response)
    if not header:
        return {
            "host": host,
            "port": int(port),
            "service": "unknown",
            "version": None,
            "evidence": "",
            "raw": "",
        }
    if "/" in header:
        service, version = header.split("/", 1)
    else:
        service, version = header, ""
    return {
        "host": host,
        "port": int(port),
        "service": service,
        "version": version,
        "evidence": header,
        "raw": "",
    }


def report():
    """按主机收成一条，端口之间互相覆盖。"""
    hosts = {}
    for host, port in sorted(_RESPONSES):
        item = identify(host, port)
        hosts[host] = {"service": item["service"], "version": item["version"]}
    return {
        "hosts": hosts,
        "ports": len(_RESPONSES),
        "unknown": [],
    }
