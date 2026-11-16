"""流量会话分析小工具。

入口：
- load(pcap, packets)            登记抓包，按五元组归并会话并解析协议；
- analyze(pcap, rule_version=…)  按会话跑版本化规则，返回命中、证据与增量明细；
- report()                       按主机/类型汇总，规则版本写入报告；
- query(...)                     告警会话按主机和时间下钻；
- compare(pcap, v1, v2)          同批包跨规则版本对比；
- mark_false_positive(...)       误报标记并回灌成用例。
"""

from .engine import (
    FEEDBACK,
    analyze,
    compare,
    get_packet,
    load,
    query,
    reset,
)
from .feedback import FeedbackStore, run_cases
from .report import report
from .rules import DEFAULT_VERSION, build_ruleset, get_ruleset, register_ruleset

__all__ = [
    "analyze",
    "load",
    "report",
    "query",
    "compare",
    "get_packet",
    "reset",
    "mark_false_positive",
    "run_cases",
    "FeedbackStore",
    "build_ruleset",
    "register_ruleset",
    "get_ruleset",
    "DEFAULT_VERSION",
    "FEEDBACK",
]


def mark_false_positive(pcap, session_id, rule_id, reason=""):
    """把指定抓包里某会话的规则命中标记为误报并回灌成用例。"""
    from .engine import _STATE

    state = _STATE.get(pcap)
    if state is None:
        raise ValueError("抓包未载入")
    for sess in state["sessions"]:
        if sess.sid == session_id:
            return FEEDBACK.mark_false_positive(sess, rule_id, reason=reason, pcap=pcap)
    raise ValueError(f"会话不存在: {session_id}")
