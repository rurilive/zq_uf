"""入口清单：同一域名下登记多个入口，支持整改后更新配置。"""

import copy
import hashlib
import json

from .errors import ConfigError, UnknownEndpointError, UnknownHostError
from .rules import KNOWN_PROTOCOLS

# 初始登记：每个域名多个入口，配置并不一致。
_SEED = {
    "shop.example.com": {
        "主入口": {
            "address": "10.1.0.11",
            "port": 443,
            "days_left": 12,
            "protocols": ["TLSv1.0", "TLSv1.1", "TLSv1.2"],
            "ciphers": ["RC4-SHA", "AES128-SHA", "ECDHE-RSA-AES256-GCM-SHA384"],
            "redirect": "https://shop.example.com/",
            "chain_complete": True,
            "chain": ["shop.example.com", "Example Intermediate CA", "Example Root CA"],
        },
        "移动端": {
            "address": "10.1.0.12",
            "port": 443,
            "days_left": 12,
            "protocols": ["TLSv1.2"],
            "ciphers": ["AES128-SHA", "ECDHE-RSA-AES128-GCM-SHA256"],
            "redirect": None,
            "chain_complete": True,
            "chain": ["shop.example.com", "Example Intermediate CA", "Example Root CA"],
        },
        "旧支付回调": {
            "address": "10.1.0.13",
            "port": 8443,
            "days_left": 3,
            "protocols": ["SSLv3", "TLSv1.0"],
            "ciphers": ["DES-CBC3-SHA", "RC4-SHA"],
            "redirect": None,
            "chain_complete": False,
            "chain": ["shop.example.com"],
        },
    },
    "api.example.com": {
        "主入口": {
            "address": "10.1.0.21",
            "port": 443,
            "days_left": 210,
            "protocols": ["TLSv1.2", "TLSv1.3"],
            "ciphers": ["ECDHE-RSA-AES128-GCM-SHA256"],
            "redirect": "https://api.example.com/",
            "chain_complete": True,
            "chain": ["api.example.com", "Example Intermediate CA", "Example Root CA"],
        },
        "内网调用": {
            "address": "10.1.0.22",
            "port": 443,
            "days_left": 210,
            "protocols": ["TLSv1.2", "TLSv1.3"],
            "ciphers": ["AES256-SHA", "ECDHE-RSA-AES128-GCM-SHA256"],
            "redirect": "https://api.example.com/",
            "chain_complete": True,
            "chain": ["api.example.com", "Example Intermediate CA", "Example Root CA"],
        },
        "IPv6 入口": {
            "address": "2001:db8::21",
            "port": 443,
            "days_left": 96,
            "protocols": ["TLSv1.0", "TLSv1.2"],
            "ciphers": ["ECDHE-RSA-AES128-GCM-SHA256"],
            "redirect": "https://api.example.com/",
            "chain_complete": True,
            "chain": ["api.example.com", "Example Intermediate CA", "Example Root CA"],
        },
    },
    "cdn.example.com": {
        "主入口": {
            "address": "10.1.0.31",
            "port": 443,
            "days_left": 45,
            "protocols": ["TLSv1.2", "TLSv1.3"],
            "ciphers": ["ECDHE-RSA-AES128-GCM-SHA256", "ECDHE-RSA-AES256-GCM-SHA384"],
            "redirect": "https://cdn.example.com/",
            "chain_complete": True,
            "chain": ["cdn.example.com", "Example Intermediate CA", "Example Root CA"],
        },
        "备用节点": {
            "address": "10.1.0.32",
            "port": 443,
            "days_left": 45,
            "protocols": ["TLSv1.2", "TLSv1.3"],
            "ciphers": ["ECDHE-RSA-AES128-GCM-SHA256", "ECDHE-RSA-AES256-GCM-SHA384"],
            "redirect": "https://cdn.example.com/",
            "chain_complete": True,
            "chain": ["cdn.example.com", "Example Intermediate CA", "Example Root CA"],
        },
    },
}

_INVENTORY = {}
_CHANGES = []


def _validate(host, name, config):
    """配置不合法就明确报错，不做静默兜底。"""
    if not isinstance(host, str) or not host:
        raise ConfigError("host 不能为空")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{host}: 入口名不能为空")
    if not isinstance(config, dict):
        raise ConfigError(f"{host}/{name}: 入口配置必须是 dict")
    address = config.get("address")
    if not isinstance(address, str) or not address:
        raise ConfigError(f"{host}/{name}: address 不能为空")
    port = config.get("port", 443)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError(f"{host}/{name}: port 必须是 1-65535 的整数")
    days_left = config.get("days_left")
    if not isinstance(days_left, int) or isinstance(days_left, bool):
        raise ConfigError(f"{host}/{name}: days_left 必须是整数")
    protocols = config.get("protocols")
    if not isinstance(protocols, (list, tuple)) or not protocols:
        raise ConfigError(f"{host}/{name}: protocols 不能为空")
    for protocol in protocols:
        if protocol not in KNOWN_PROTOCOLS:
            raise ConfigError(f"{host}/{name}: 未知协议版本 {protocol!r}")
    ciphers = config.get("ciphers")
    if not isinstance(ciphers, (list, tuple)) or not ciphers:
        raise ConfigError(f"{host}/{name}: ciphers 不能为空")
    for cipher in ciphers:
        if not isinstance(cipher, str) or not cipher:
            raise ConfigError(f"{host}/{name}: 套件名不能为空")
    redirect = config.get("redirect")
    if redirect is not None and not isinstance(redirect, str):
        raise ConfigError(f"{host}/{name}: redirect 必须是 URL 字符串或 None")
    chain = config.get("chain", [])
    if not isinstance(chain, (list, tuple)):
        raise ConfigError(f"{host}/{name}: chain 必须是列表")
    chain_complete = config.get("chain_complete", True)
    if not isinstance(chain_complete, bool):
        raise ConfigError(f"{host}/{name}: chain_complete 必须是布尔值")


def _normalize(config):
    return {
        "address": config["address"],
        "port": config.get("port", 443),
        "days_left": config["days_left"],
        "protocols": list(config["protocols"]),
        "ciphers": list(config["ciphers"]),
        "redirect": config.get("redirect"),
        "chain_complete": config.get("chain_complete", True),
        "chain": list(config.get("chain", [])),
    }


def reset():
    """恢复初始登记并清空整改记录。"""
    global _INVENTORY
    _INVENTORY = copy.deepcopy(_SEED)
    _CHANGES.clear()


def register(host, endpoint, config):
    """登记或覆盖一个入口，返回入口快照。"""
    _validate(host, endpoint, config)
    entry = _normalize(config)
    _INVENTORY.setdefault(host, {})[endpoint] = entry
    _CHANGES.append({"action": "register", "host": host, "endpoint": endpoint,
                     "fingerprint": fingerprint_of(entry)})
    return copy.deepcopy(entry)


def update(host, endpoint, **changes):
    """整改后更新入口配置，返回更新前后差异。"""
    current = get_endpoint(host, endpoint)
    unknown = sorted(set(changes) - set(current))
    if unknown:
        raise ConfigError(f"{host}/{endpoint}: 未知字段 {', '.join(unknown)}")
    merged = dict(current)
    merged.update(changes)
    _validate(host, endpoint, merged)
    updated = _normalize(merged)
    changed = [field for field in updated if updated[field] != current.get(field)]
    _INVENTORY[host][endpoint] = updated
    _CHANGES.append({"action": "update", "host": host, "endpoint": endpoint,
                     "fields": changed, "fingerprint": fingerprint_of(updated)})
    return {"host": host, "endpoint": endpoint, "changed": changed,
            "before": copy.deepcopy(current), "after": copy.deepcopy(updated)}


def remove(host, endpoint):
    """注销一个入口，返回被移除的配置。"""
    entry = get_endpoint(host, endpoint)
    del _INVENTORY[host][endpoint]
    if not _INVENTORY[host]:
        del _INVENTORY[host]
    _CHANGES.append({"action": "remove", "host": host, "endpoint": endpoint})
    return entry


def hosts():
    return sorted(_INVENTORY)


def endpoints(host):
    """入口名按登记顺序返回，初始数据顺序固定。"""
    if host not in _INVENTORY:
        raise UnknownHostError(f"unknown host: {host}")
    return list(_INVENTORY[host])


def has_host(host):
    return host in _INVENTORY


def get_endpoint(host, endpoint):
    if host not in _INVENTORY:
        raise UnknownHostError(f"unknown host: {host}")
    if endpoint not in _INVENTORY[host]:
        raise UnknownEndpointError(f"unknown endpoint: {host}/{endpoint}")
    return copy.deepcopy(_INVENTORY[host][endpoint])


def changes():
    """整改记录，按发生顺序。"""
    return copy.deepcopy(_CHANGES)


def fingerprint_of(config):
    """配置指纹，用于判断入口是否发生变化。"""
    payload = json.dumps(config, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


reset()
