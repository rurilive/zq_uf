"""带版本的检测规则集。

规则对会话出结论而不是对包出结论，四类判定：
- lateral_scan  横向扫描：内网主机短时间内触达大量内网主机/端口；
- external_conn 外联：内网主机主动访问公网；
- tunnel        隧道：异常 TLS/SSH 端口或 DNS 长查询等隧道特征；
- abnormal_port 异常端口：非标准端口承载已知协议或会话端口不在基线内。

每条规则带 id/version/fingerprint，规则变更后引擎按指纹只重跑受影响会话。
"""

import hashlib
from dataclasses import dataclass

# 结论展示优先级：一条会话可能同时命中多条规则，主结论取优先级最高者。
TYPE_PRIORITY = {
    "lateral_scan": 0,
    "tunnel": 1,
    "external_conn": 2,
    "abnormal_port": 3,
}


@dataclass
class RuleContext:
    """规则求值上下文：单会话视角 + 同源主机的跨会话聚合。"""
    session: object
    by_source: dict          # src ip -> {"hosts": set, "sessions": [sess...], "ports": set}
    config: dict


def _evidence(session, limit=3):
    """取会话前若干个包作为证据，保证可回放且结果稳定。"""
    return session.packets[:limit]


def _fingerprint(rule_id, version, config):
    # 指纹只取决于规则 id 与配置内容：版本号升级但配置没变的规则视为未变更，
    # 引擎可直接复用上次结论（结论中的版本号在复用处刷新）。
    raw = f"{rule_id}|{sorted(config.items()) if config else []}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


class Rule:
    type = "unknown"
    scope = "session"          # session 规则只看单会话；cross 规则看聚合上下文

    def __init__(self, rule_id, version, config=None):
        self.rule_id = rule_id
        self.version = version
        self.config = dict(config or {})
        self.fingerprint = _fingerprint(rule_id, version, self.config)

    def evaluate(self, ctx):
        """返回 (hit 字典或 None, 受影响会话列表)。

        cross 规则一次评估会影响多会话，第二个返回值列出它实际改写的会话，
        引擎据此一次性更新，避免重复求值。
        """
        raise NotImplementedError


class LateralScanRule(Rule):
    type = "lateral_scan"
    scope = "cross"

    def evaluate(self, ctx):
        info = ctx.by_source.get(ctx.session.src)
        if info is None:
            return None, []
        threshold = self.config["scan_host_threshold"]
        allowed_dsts = set(self.config.get("allowed_dst", ()))
        # 扇出只统计内网目标，备份服务器等白名单目标不计入；
        # 代表会话可能是该源的外联会话，因此方向判断落在 affected 上而不是代表上。
        lateral_sessions = [
            sess for sess in info["sessions"]
            if sess.direction == "lateral" and sess.dst not in allowed_dsts
        ]
        targets = {sess.dst for sess in lateral_sessions}
        if len(targets) < threshold:
            return None, []
        hit = {
            "type": self.type,
            "rule_id": self.rule_id,
            "rule_version": self.version,
            "priority": TYPE_PRIORITY[self.type],
            "reason": f"内网扇出 {len(targets)} 台主机，阈值 {threshold}",
            "evidence": _evidence(ctx.session),
            "confidence": "high",
        }
        return hit, lateral_sessions


class ExternalConnRule(Rule):
    type = "external_conn"
    scope = "session"

    def evaluate(self, ctx):
        sess = ctx.session
        if sess.direction != "outbound":
            return None, []
        allowed = set(self.config.get("allowed_dst", ()))
        if sess.dst in allowed:
            return None, []
        return {
            "type": self.type,
            "rule_id": self.rule_id,
            "rule_version": self.version,
            "priority": TYPE_PRIORITY[self.type],
            "reason": f"内网主机 {sess.src} 主动外联 {sess.dst}:{sess.dport}",
            "evidence": _evidence(sess),
            "confidence": "medium",
        }, [sess]


class TunnelRule(Rule):
    type = "tunnel"
    scope = "session"

    def evaluate(self, ctx):
        sess = ctx.session
        tls_ports = set(self.config["tls_ports"])
        ssh_ports = set(self.config["ssh_ports"])
        dns_query_len = self.config["dns_query_len"]

        reasons = []
        if sess.app == "tls" and sess.dport not in tls_ports:
            reasons.append(f"TLS 跑在异常端口 {sess.dport}")
        if sess.app == "ssh" and sess.dport not in ssh_ports:
            reasons.append(f"SSH 跑在异常端口 {sess.dport}")
        for item in sess.payloads:
            if item.get("query_len", 0) >= dns_query_len:
                reasons.append(
                    f"DNS 查询长度 {item['query_len']} 超阈值 {dns_query_len}，疑似 DNS 隧道"
                )
        if not reasons:
            return None, []
        return {
            "type": self.type,
            "rule_id": self.rule_id,
            "rule_version": self.version,
            "priority": TYPE_PRIORITY[self.type],
            "reason": "；".join(reasons),
            "evidence": _evidence(sess),
            "confidence": "medium",
        }, [sess]


class AbnormalPortRule(Rule):
    type = "abnormal_port"
    scope = "session"

    def evaluate(self, ctx):
        sess = ctx.session
        baseline = set(self.config["baseline_ports"])
        backup_ports = set(self.config.get("backup_ports", ()))
        # 备份业务端口（如 SMB 445）属于已知业务，不再误报为异常端口
        if sess.dport in backup_ports:
            return None, []
        if sess.dport in baseline:
            return None, []
        if sess.app != "unknown" and sess.dport not in baseline:
            reason = f"{sess.app} 协议跑在非基线端口 {sess.dport}"
        else:
            reason = f"端口 {sess.dport} 不在基线内且协议未识别"
        return {
            "type": self.type,
            "rule_id": self.rule_id,
            "rule_version": self.version,
            "priority": TYPE_PRIORITY[self.type],
            "reason": reason,
            "evidence": _evidence(sess),
            "confidence": "low",
        }, [sess]


@dataclass
class Ruleset:
    name: str
    version: str
    config: dict
    rules: list
    internal_nets: tuple = ()
    port_map: dict = None

    @property
    def id(self):
        return f"{self.name}@{self.version}"

    def fingerprints(self):
        return {rule.rule_id: rule.fingerprint for rule in self.rules}


def _config_v1():
    return {
        "lateral_scan": {
            "scan_host_threshold": 5,
            "allowed_dst": ("10.0.0.9",),          # 备份服务器，白名单
        },
        "external_conn": {
            "allowed_dst": (),
        },
        "tunnel": {
            # 隧道视角只承认标准端口；TLS/SSH 跑到备用或任意高端口即疑似隧道
            "tls_ports": (443,),
            "ssh_ports": (22,),
            "dns_query_len": 60,
        },
        "abnormal_port": {
            "baseline_ports": (22, 53, 80, 443, 445, 8443, 8080),
            "backup_ports": (445, 139),
        },
    }


def build_ruleset(version, name="default", overrides=None, internal_nets=()):
    """按版本构建规则集；overrides 只覆盖给定规则的配置项。"""
    if version == "1.0.0":
        config = _config_v1()
    elif version == "1.1.0":
        # 1.1.0：横向扫描阈值从 5 收紧到 3，隧道 DNS 查询长度阈值收紧到 40
        config = _config_v1()
        config["lateral_scan"]["scan_host_threshold"] = 3
        config["tunnel"]["dns_query_len"] = 40
    else:
        raise ValueError(f"未知规则版本: {version}")

    if overrides:
        for rule_id, patch in overrides.items():
            if rule_id not in config:
                raise ValueError(f"未知规则: {rule_id}")
            config[rule_id].update(patch)

    rules = [
        LateralScanRule("lateral_scan", version, config["lateral_scan"]),
        ExternalConnRule("external_conn", version, config["external_conn"]),
        TunnelRule("tunnel", version, config["tunnel"]),
        AbnormalPortRule("abnormal_port", version, config["abnormal_port"]),
    ]
    return Ruleset(name=name, version=version, config=config, rules=rules,
                   internal_nets=tuple(internal_nets))


# 规则版本注册表：自定义版本通过 register_ruleset 接入，analyze 按版本号取用。
_REGISTRY = {}


def register_ruleset(ruleset):
    _REGISTRY[ruleset.id] = ruleset
    return ruleset


def get_ruleset(version, name="default"):
    if version is None:
        version = DEFAULT_VERSION
    key = f"{name}@{version}"
    if key in _REGISTRY:
        return _REGISTRY[key]
    return build_ruleset(version, name=name)


DEFAULT_VERSION = "1.1.0"
