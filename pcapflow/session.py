"""会话归并：按规范化五元组把逐包流合并成会话。

一个 Session 记录连接两端、方向、协议解析结果、全部原始包序号，
以及每条规则命中时引用的证据包序号，证据序号可直接回到原始抓包回放。
"""

from dataclasses import dataclass, field

from .netutils import NetworkView, canonical_key, classify_direction, session_id


@dataclass
class Session:
    sid: str
    key: tuple
    proto: str
    src: str
    sport: int
    dst: str
    dport: int
    direction: str
    packets: list = field(default_factory=list)          # 原始包序号（按抓包顺序）
    first_seen: float = 0.0
    last_seen: float = 0.0
    packet_count: int = 0
    byte_count: int = 0
    apps: list = field(default_factory=list)              # 出现过的应用协议
    app: str = "unknown"                                  # 主应用协议
    uncovered: list = field(default_factory=list)         # 无法识别协议的包序号
    payloads: list = field(default_factory=list)          # 供规则做特征判断
    verdict: dict = field(default_factory=dict)           # 规则 id -> hit 字典
    suppressed: list = field(default_factory=list)        # 被误报用例抑制的规则 id

    @property
    def types(self):
        """该会话命中的全部判定类型（保留多结论，不互相覆盖）。"""
        return [hit["type"] for hit in self.verdict.values()]

    @property
    def primary_type(self):
        """主结论：取最高优先级命中，供按主机分类汇总。"""
        if not self.verdict:
            return None
        return min(self.verdict.values(), key=lambda h: h["priority"])["type"]

    def as_dict(self):
        return {
            "session_id": self.sid,
            "proto": self.proto,
            "src": self.src,
            "sport": self.sport,
            "dst": self.dst,
            "dport": self.dport,
            "direction": self.direction,
            "app": self.app,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "packet_count": self.packet_count,
            "byte_count": self.byte_count,
            "packets": list(self.packets),
            "types": self.types,
            "primary_type": self.primary_type,
            "suppressed": list(self.suppressed),
        }


def _require(packet, name):
    value = packet.get(name)
    if value is None or value == "":
        raise ValueError(f"包缺少字段: {name}")
    return value


def build_sessions(packets, view=None, protocols=None):
    """把包列表归并成会话，并在归并过程中完成协议解析。

    protocols 为 Protocols 注册表；解析失败的包序号会在返回值里单独归集，
    未覆盖的协议（注册表识别不出来）挂在会话的 uncovered 上。
    """
    view = view or NetworkView()
    sessions = {}
    order = []
    failed = []

    for packet in packets:
        if not isinstance(packet, dict):
            raise ValueError("包记录必须是字典")
        no = int(_require(packet, "no"))
        src = str(_require(packet, "src"))
        dst = str(_require(packet, "dst"))
        dport = int(_require(packet, "dport"))
        sport = int(packet.get("sport", 0) or 0)
        proto = str(packet.get("proto", "tcp")).lower()
        ts = float(packet.get("ts", 0.0) or 0.0)
        size = int(packet.get("size", 0) or 0)

        key = canonical_key(proto, src, sport, dst, dport)
        sess = sessions.get(key)
        if sess is None:
            direction = classify_direction(src, dst, view)
            sess = Session(
                sid=session_id(key),
                key=key,
                proto=proto,
                src=src,
                sport=sport,
                dst=dst,
                dport=dport,
                direction=direction,
                first_seen=ts,
            )
            sessions[key] = sess
            order.append(key)
        if ts and not sess.last_seen:
            sess.first_seen = ts
        sess.last_seen = max(sess.last_seen, ts) if ts else sess.last_seen
        sess.packets.append(no)
        sess.packet_count += 1
        sess.byte_count += size

        if protocols is not None:
            result = protocols.parse(packet, sess)
            if result.status == "failed":
                failed.append({
                    "packet": no,
                    "proto": result.proto,
                    "app": result.app,
                    "reason": result.reason,
                    "session_id": sess.sid,
                })
            if result.app and result.app not in sess.apps:
                sess.apps.append(result.app)
            if result.status == "uncovered":
                sess.uncovered.append(no)
            sess.app = result.app or sess.app
            if result.meta is not None:
                sess.payloads.append({"packet": no, **result.meta})

    return [sessions[key] for key in order], failed
