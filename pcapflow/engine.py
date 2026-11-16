"""分析引擎：load / analyze 入口与增量重跑。

数据流：原始包 -> 五元组会话 + 协议解析（抓包内容不变则缓存复用）
       -> 规则集逐会话出结论（按规则指纹增量，只重跑变更规则）
       -> 误报抑制 -> 命中/证据/未覆盖/失败归集。

规则改一次只重跑受影响规则涉及的会话，incremental 字段说明本次复用与
重跑的明细，跑完能不能对得上差查看这里即可。
"""

import hashlib

from . import rules as rules_mod
from .feedback import FeedbackStore
from .netutils import NetworkView
from .protocols import Protocols
from .rules import RuleContext, get_ruleset
from .session import build_sessions

# pcap -> 抓包与分析状态
_STATE = {}
# pcap -> 最近一次 analyze 输出，供 report / query 下钻
_LAST_OUTPUTS = {}
# 全局误报用例库（会话 ID 由五元组决定，跨重跑稳定）
FEEDBACK = FeedbackStore()


def _packet_hash(packets):
    raw = repr(sorted((p.get("no"), p.get("src"), p.get("sport"),
                       p.get("dst"), p.get("dport"), p.get("proto")) for p in packets))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def load(pcap, packets, internal_nets=None, port_map=None):
    """登记抓包。同内容重复载入保留会话与规则缓存；内容变化则缓存失效。"""
    if not isinstance(pcap, str) or not pcap:
        raise ValueError("pcap 不能为空")
    if not isinstance(packets, list) or not packets:
        raise ValueError("packets 不能为空")
    rows = []
    for packet in packets:
        if not isinstance(packet, dict):
            raise ValueError("包记录必须是字典")
        rows.append(dict(packet))

    digest = _packet_hash(rows)
    state = _STATE.get(pcap)
    if state is not None and state["packet_hash"] == digest:
        return {"pcap": pcap, "packets": len(rows), "cached": True}

    view = NetworkView(internal_nets) if internal_nets else NetworkView()
    protocols = Protocols(port_map)
    sessions, failed = build_sessions(rows, view=view, protocols=protocols)
    _STATE[pcap] = {
        "packets": rows,
        "packet_hash": digest,
        "view": view,
        "protocols": protocols,
        "sessions": sessions,
        "failed": failed,
        # rule_id -> {"fingerprint": str, "hits": {sid: hit 或 None}}
        "rule_cache": {},
        "last_ruleset": None,
    }
    return {"pcap": pcap, "packets": len(rows), "cached": False}


def _build_by_source(sessions):
    by_source = {}
    for sess in sessions:
        info = by_source.setdefault(sess.src, {"hosts": set(), "ports": set(), "sessions": []})
        info["hosts"].add(sess.dst)
        info["ports"].add(sess.dport)
        info["sessions"].append(sess)
    return by_source


def _materialize(hit, sess):
    """跨会话规则的命中模板落到具体会话，证据换成该会话自己的包序号。"""
    item = dict(hit)
    item["session_id"] = sess.sid
    item["evidence"] = sess.packets[:3]
    return item


def _evaluate_ruleset(state, ruleset):
    sessions = state["sessions"]
    by_source = _build_by_source(sessions)
    cache = state["rule_cache"]
    reused_rules, rerun_rules = [], []
    reused_sessions, rerun_sessions = 0, 0

    for sess in sessions:
        sess.verdict.clear()

    for rule in ruleset.rules:
        cached = cache.get(rule.rule_id)
        if cached is not None and cached["fingerprint"] == rule.fingerprint:
            index = _sid_index(sessions)
            for sid, hit in cached["hits"].items():
                if hit is not None:
                    reused_hit = dict(hit)
                    reused_hit["rule_version"] = rule.version
                    index[sid].verdict[rule.rule_id] = reused_hit
            reused_rules.append(rule.rule_id)
            reused_sessions += len(cached["hits"])
            continue

        hits_by_sid = {}
        if rule.scope == "session":
            for sess in sessions:
                hit, _ = rule.evaluate(RuleContext(sess, by_source, rule.config))
                hits_by_sid[sess.sid] = hit
                if hit is not None:
                    sess.verdict[rule.rule_id] = _materialize(hit, sess)
            rerun_sessions += len(sessions)
        else:
            # cross 规则：按源主机分组评估，一次覆盖该源的全部相关会话
            for source, info in by_source.items():
                representative = info["sessions"][0]
                hit, affected = rule.evaluate(
                    RuleContext(representative, by_source, rule.config)
                )
                for sess in affected:
                    per_hit = _materialize(hit, sess) if hit else None
                    hits_by_sid[sess.sid] = per_hit
                    if per_hit is not None:
                        sess.verdict[rule.rule_id] = per_hit
            rerun_sessions += len(sessions)
        cache[rule.rule_id] = {"fingerprint": rule.fingerprint, "hits": hits_by_sid}
        rerun_rules.append(rule.rule_id)

    state["last_ruleset"] = ruleset.id
    return {
        "reused_rules": reused_rules,
        "rerun_rules": rerun_rules,
        "reused_sessions": reused_sessions,
        "rerun_sessions": rerun_sessions,
    }


def _sid_index(sessions):
    return {sess.sid: sess for sess in sessions}


def _hit_record(sess, hit):
    return {
        "session_id": sess.sid,
        "src": sess.src,
        "sport": sess.sport,
        "dst": sess.dst,
        "dport": sess.dport,
        "proto": sess.proto,
        "app": sess.app,
        "direction": sess.direction,
        "type": hit["type"],
        "rule_id": hit["rule_id"],
        "rule_version": hit["rule_version"],
        "reason": hit["reason"],
        "confidence": hit.get("confidence", ""),
        "evidence": list(hit["evidence"]),
        "first_seen": sess.first_seen,
    }


def _uncovered(sessions):
    out = []
    for sess in sessions:
        if sess.uncovered:
            out.append({
                "session_id": sess.sid,
                "proto": sess.proto,
                "dst": sess.dst,
                "dport": sess.dport,
                "packets": list(sess.uncovered),
            })
    return out


def analyze(pcap, rule_version=None, ruleset_name="default", use_feedback=True,
            feedback_store=None):
    """按会话和协议解析跑检测，返回命中会话、判定类型、证据包序号与规则版本。

    feedback_store 可注入独立用例库（回归测试用）；默认用全局 FEEDBACK。
    """
    state = _STATE.get(pcap)
    if state is None:
        raise ValueError("抓包未载入")
    ruleset = get_ruleset(
        rule_version or rules_mod.DEFAULT_VERSION, name=ruleset_name
    )
    sessions = state["sessions"]

    incremental = _evaluate_ruleset(state, ruleset)
    if use_feedback:
        (feedback_store or FEEDBACK).apply(sessions)

    hits, hit_sids = [], set()
    for sess in sessions:
        for rule_id, hit in sess.verdict.items():
            if rule_id in sess.suppressed:
                continue
            hits.append(_hit_record(sess, hit))
            hit_sids.add(sess.sid)

    known_apps = sorted({app for sess in sessions for app in sess.apps if app})
    out = {
        "pcap": pcap,
        "rule_version": ruleset.version,
        "ruleset_id": ruleset.id,
        "sessions": len(sessions),
        "hit_sessions": len(hit_sids),
        "hits": hits,
        "session_list": [sess.as_dict() for sess in sessions],
        "protocols": known_apps,
        "uncovered": _uncovered(sessions),
        "failed": [dict(item) for item in state["failed"]],
        "incremental": incremental,
        "_sessions": sessions,
    }
    _LAST_OUTPUTS[pcap] = out
    return out


def get_packet(pcap, no):
    """按原始包序号取回包记录，供证据回放。"""
    state = _STATE.get(pcap)
    if state is None:
        raise ValueError("抓包未载入")
    for packet in state["packets"]:
        if int(packet.get("no", -1)) == int(no):
            return dict(packet)
    raise ValueError(f"包序号不存在: {no}")


def query(pcap=None, host=None, start=None, end=None, direction=None, type=None):
    """告警命中会话下钻：按抓包/主机/时间窗/方向/判定类型过滤。"""
    pcaps = [pcap] if pcap else list(_LAST_OUTPUTS)
    rows = []
    for name in pcaps:
        out = _LAST_OUTPUTS.get(name)
        if out is None:
            continue
        for hit in out["hits"]:
            if host and host not in (hit["src"], hit["dst"]):
                continue
            if start is not None and hit["first_seen"] < start:
                continue
            if end is not None and hit["first_seen"] > end:
                continue
            if direction and hit["direction"] != direction:
                continue
            if type and hit["type"] != type:
                continue
            rows.append({"pcap": name, **hit})
    return rows


def compare(pcap, version_a, version_b, ruleset_name="default"):
    """同一批包用两个规则版本跑，对比命中差异，验证规则改动效果。"""
    out_a = analyze(pcap, rule_version=version_a, ruleset_name=ruleset_name)
    out_b = analyze(pcap, rule_version=version_b, ruleset_name=ruleset_name)

    def index(out):
        return {(h["session_id"], h["rule_id"]): h for h in out["hits"]}

    idx_a, idx_b = index(out_a), index(out_b)
    added = [idx_b[k] for k in idx_b.keys() - idx_a.keys()]
    removed = [idx_a[k] for k in idx_a.keys() - idx_b.keys()]
    changed = []
    for k in idx_a.keys() & idx_b.keys():
        ha, hb = idx_a[k], idx_b[k]
        if ha["reason"] != hb["reason"] or ha["type"] != hb["type"]:
            changed.append({
                "session_id": k[0],
                "rule_id": k[1],
                "from": {"type": ha["type"], "reason": ha["reason"]},
                "to": {"type": hb["type"], "reason": hb["reason"]},
            })
    return {
        "pcap": pcap,
        "versions": [version_a, version_b],
        "summary": {
            "hits_a": len(idx_a),
            "hits_b": len(idx_b),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "added": sorted(added, key=lambda h: (h["session_id"], h["rule_id"])),
        "removed": sorted(removed, key=lambda h: (h["session_id"], h["rule_id"])),
        "changed": sorted(changed, key=lambda h: (h["session_id"], h["rule_id"])),
        "failed_delta": len(out_b["failed"]) - len(out_a["failed"]),
    }


def reset():
    """清空全部状态，主要给测试用。"""
    _STATE.clear()
    _LAST_OUTPUTS.clear()
    FEEDBACK._cases.clear()
