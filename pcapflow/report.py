"""汇总报告：按主机查看、按类型统计、规则版本、未覆盖与解析失败。"""

from . import rules as rules_mod
from .engine import _LAST_OUTPUTS


def report(pcap=None):
    """生成报告。pcap 为空时汇总全部已分析抓包。

    hosts 下每个主机给出命中数、按类型分布和涉及的会话，安全同学直接
    按主机排查；rule_version 写进报告，跨版本结果可追溯、可对比。
    """
    pcaps = [pcap] if pcap else list(_LAST_OUTPUTS)
    outputs = [_LAST_OUTPUTS[name] for name in pcaps if name in _LAST_OUTPUTS]
    if not outputs:
        return {
            "pcaps": [],
            "hits": 0,
            "types": {},
            "hosts": {},
            "rule_versions": [],
            "failed": 0,
            "uncovered": 0,
        }

    types, hosts = {}, {}
    total_hits, total_failed, total_uncovered = 0, 0, 0
    versions = []
    for out in outputs:
        versions.append({"pcap": out["pcap"], "rule_version": out["rule_version"]})
        total_failed += len(out["failed"])
        total_uncovered += len(out["uncovered"])
        for hit in out["hits"]:
            total_hits += 1
            types[hit["type"]] = types.get(hit["type"], 0) + 1
            host = hosts.setdefault(hit["src"], {
                "hits": 0,
                "types": {},
                "sessions": [],
                "outbound_dst": set(),
            })
            host["hits"] += 1
            host["types"][hit["type"]] = host["types"].get(hit["type"], 0) + 1
            if hit["session_id"] not in host["sessions"]:
                host["sessions"].append(hit["session_id"])
            if hit["direction"] == "outbound":
                host["outbound_dst"].add(hit["dst"])

    for entry in hosts.values():
        entry["outbound_dst"] = sorted(entry["outbound_dst"])

    return {
        "pcaps": [out["pcap"] for out in outputs],
        "rule_versions": versions,
        "rule_version": outputs[-1]["rule_version"] or rules_mod.DEFAULT_VERSION,
        "hits": total_hits,
        "types": dict(sorted(types.items())),
        "hosts": dict(sorted(hosts.items())),
        "failed": total_failed,
        "failed_packets": [item for out in outputs for item in out["failed"]],
        "uncovered": total_uncovered,
        "uncovered_sessions": [item for out in outputs for item in out["uncovered"]],
    }
