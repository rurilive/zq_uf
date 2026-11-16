"""按单个包匹配规则，同一会话会重复计数。"""

_PCAPS = {}
_LAST = {"hits": []}

_DEFAULT_PORTS = (80, 443, 22)


def load(pcap, packets):
    """登记抓包里的包列表。"""
    if not isinstance(pcap, str) or not pcap:
        raise ValueError("pcap 不能为空")
    if not isinstance(packets, list) or not packets:
        raise ValueError("packets 不能为空")
    rows = []
    for packet in packets:
        if not isinstance(packet, dict):
            raise ValueError("包记录必须是字典")
        rows.append(dict(packet))
    _PCAPS[pcap] = rows
    return {"pcap": pcap, "packets": len(rows)}


def analyze(pcap):
    """逐包看目的端口，不在白名单就记一条。"""
    packets = _PCAPS.get(pcap)
    if packets is None:
        raise ValueError("抓包未载入")
    hits = []
    for packet in packets:
        port = packet.get("dport", 0)
        if port not in _DEFAULT_PORTS:
            hits.append({
                "src": packet.get("src", ""),
                "dst": packet.get("dst", ""),
                "dport": port,
                "proto": packet.get("proto", "tcp"),
                "type": "abnormal_port",
                "packet": packet.get("no", 0),
            })
    _LAST["hits"] = hits
    return {
        "pcap": pcap,
        "hits": [dict(hit) for hit in hits],
        "sessions": len(hits),
        "rule_version": "",
        "protocols": sorted({str(packet.get("proto", "tcp")) for packet in packets}),
        "failed": [],
    }


def report():
    """按类型和主机汇总命中。"""
    types = {}
    hosts = {}
    for hit in _LAST["hits"]:
        types[hit["type"]] = types.get(hit["type"], 0) + 1
        hosts[hit["src"]] = hosts.get(hit["src"], 0) + 1
    return {
        "hits": len(_LAST["hits"]),
        "types": types,
        "hosts": hosts,
        "rule_version": "",
        "failed": 0,
    }
