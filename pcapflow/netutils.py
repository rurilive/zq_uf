"""IP 归属与五元组规范化。

会话归并的关键是把方向相反的包映射到同一个 key，因此五元组在归并前
先做端点排序，排序结果与抓包顺序无关，只与连接本身有关。
"""

import hashlib
import ipaddress

# 默认视为内网的网段：RFC1918、回环、链路本地、IPv6 唯一本地地址等。
DEFAULT_INTERNAL_NETS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)


def _parse_network(spec):
    try:
        return ipaddress.ip_network(spec, strict=False)
    except ValueError as exc:
        raise ValueError(f"非法网段: {spec}") from exc


class NetworkView:
    """判断一个 IP 是否属于内网，网段可按抓包单独覆盖。"""

    def __init__(self, nets=DEFAULT_INTERNAL_NETS):
        self._nets = tuple(_parse_network(n) for n in nets)

    def is_internal(self, ip):
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in self._nets)


def classify_direction(src, dst, view):
    """返回 lateral / outbound / inbound / external 四种方向。"""
    src_in = view.is_internal(src)
    dst_in = view.is_internal(dst)
    if src_in and dst_in:
        return "lateral"
    if src_in and not dst_in:
        return "outbound"
    if not src_in and dst_in:
        return "inbound"
    return "external"


def canonical_key(proto, src, sport, dst, dport):
    """五元组规范化：两端点按 (ip, port) 排序，正反方向归并到同一 key。"""
    a = (str(src), int(sport))
    b = (str(dst), int(dport))
    left, right = sorted((a, b))
    return str(proto).lower(), left[0], left[1], right[0], right[1]


def session_id(key):
    """由规范化五元组生成稳定会话 ID，同一批包重跑结果不变。"""
    raw = "|".join(str(part) for part in key)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
