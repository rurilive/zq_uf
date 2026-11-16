"""协议解析注册表与内置解析器。

解析分三种结果：
- ok：端口命中注册表，或载荷特征匹配；
- failed：注册表认为该端口/提示属于某协议，但载荷与协议矛盾（畸形包）；
- uncovered：注册表和载荷嗅探都识别不出来，属于未覆盖协议。

没有载荷时按端口识别不算失败，抓包工具不保留 payload 是常态。
"""

from dataclasses import dataclass
from typing import Optional

# 传输层协议之外的应用协议默认端口，规则集也可以扩展这份映射。
DEFAULT_PORT_MAP = {
    "http": (80, 8080, 8000),
    "tls": (443, 8443),
    "dns": (53, 5353),
    "ssh": (22,),
    "smb": (445, 139),
}

_HTTP_METHODS = (
    b"GET ", b"POST ", b"PUT ", b"DELETE ", b"HEAD ",
    b"OPTIONS ", b"PATCH ", b"CONNECT ", b"TRACE ",
)


@dataclass
class ParseResult:
    status: str                 # ok / failed / uncovered
    proto: str
    app: str = ""
    reason: str = ""
    meta: Optional[dict] = None

    @classmethod
    def uncovered(cls, proto):
        return cls("uncovered", proto)

    @classmethod
    def ok(cls, proto, app, meta=None):
        return cls("ok", proto, app, meta=meta)

    @classmethod
    def failed(cls, proto, app, reason):
        return cls("failed", proto, app, reason=reason)


def _as_bytes(payload):
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        if payload.startswith("hex:"):
            return bytes.fromhex(payload[4:])
        return payload.encode("utf-8", errors="replace")
    return None


# --- 内置解析器：返回 (失败原因或 None, 特征元数据) -------------------------

def _parse_http(packet, payload):
    meta = {"host": packet.get("http_host")}
    if payload is None:
        return None, meta
    if payload.startswith(b"HTTP/") or any(payload.startswith(m) for m in _HTTP_METHODS):
        return None, meta
    return "载荷不是合法 HTTP 请求/响应", meta


def _parse_tls(packet, payload):
    meta = {"sni": packet.get("tls_sni") or packet.get("sni")}
    if payload is None:
        return None, meta
    # TLS 记录层：content-type=0x16(handshake) 或 0x17(app data)，版本 0x03xx
    if payload and payload[0] in (0x14, 0x15, 0x16, 0x17) and len(payload) >= 3 and payload[1] == 0x03:
        return None, meta
    return "TLS 记录层头不合法", meta


def _parse_dns(packet, payload):
    query = packet.get("dns_query")
    meta = {
        "query": query,
        "query_len": len(query) if isinstance(query, str) else 0,
    }
    if payload is None:
        return None, meta
    if len(payload) < 12:
        return "DNS 报文短于 12 字节头部", meta
    qdcount = int.from_bytes(payload[4:6], "big")
    if qdcount == 0:
        return "DNS 问题计数为 0", meta
    return None, meta


def _parse_ssh(packet, payload):
    if payload is None:
        return None, {}
    if payload.startswith(b"SSH-"):
        return None, {"banner": payload.split(b"\r\n", 1)[0].decode("ascii", "replace")}
    return "缺少 SSH 版本标识", {}


def _parse_smb(packet, payload):
    if payload is None:
        return None, {}
    if payload.startswith((b"\xffSMB", b"\xfeSMB")):
        return None, {}
    # NetBIOS Session Service：首字节 0x00，随后 3 字节长度，再跟 SMB 头
    if len(payload) >= 4 and payload[0] == 0x00 and payload[3:4] in (b"\xff", b"\xfe"):
        return None, {}
    return "SMB/NBSS 报文头不合法", {}


_BUILTIN_PARSERS = {
    "http": (_parse_http,),
    "tls": (_parse_tls,),
    "dns": (_parse_dns,),
    "ssh": (_parse_ssh,),
    "smb": (_parse_smb,),
}


class Protocols:
    """应用协议注册表：端口映射 + 提示字段 + 载荷嗅探。"""

    def __init__(self, port_map=None):
        self.port_map = {app: tuple(ports) for app, ports in (port_map or DEFAULT_PORT_MAP).items()}
        self._parsers = dict(_BUILTIN_PARSERS)

    def register(self, app, ports, parser):
        """扩展自定义协议解析器，parser 签名与内置解析器一致。"""
        self.port_map[app] = tuple(ports)
        self._parsers[app] = (parser,)

    @property
    def known_apps(self):
        return tuple(sorted(self._parsers))

    def _app_by_port(self, port):
        for app, ports in self.port_map.items():
            if port in ports:
                return app
        return None

    def _run(self, app, packet, payload):
        parser = self._parsers.get(app)
        if parser is None:
            return ParseResult.ok(packet.get("proto", "tcp"), app)
        reason, meta = parser[0](packet, payload)
        proto = str(packet.get("proto", "tcp")).lower()
        if reason is not None:
            return ParseResult.failed(proto, app, reason)
        return ParseResult.ok(proto, app, meta or {})

    def parse(self, packet, session=None):
        proto = str(packet.get("proto", "tcp")).lower()
        payload = _as_bytes(packet.get("payload"))
        hinted = packet.get("app")

        # 1) 显式提示优先：提示的协议已注册才解析，否则交给后续识别
        if isinstance(hinted, str) and hinted in self._parsers:
            return self._run(hinted, packet, payload)

        # 2) 端口识别（双向都查，兼顾反向包）
        app = self._app_by_port(int(packet.get("dport", 0) or 0))
        if app is None:
            app = self._app_by_port(int(packet.get("sport", 0) or 0))
        if app is not None:
            return self._run(app, packet, payload)

        # 3) 载荷嗅探：没有任何端口线索时靠特征猜
        if payload is not None:
            for candidate in self._parsers:
                result = self._run(candidate, packet, payload)
                if result.status == "ok":
                    return result

        return ParseResult.uncovered(proto)
