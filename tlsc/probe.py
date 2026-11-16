"""证书与协议核查：覆盖同一域名的全部入口，按入口给结论。"""

from . import inventory, rules
from .errors import UnknownEndpointError, UnknownHostError


REMIND_DAYS = 30
"""默认提前提醒天数：剩余有效期不超过该值就列入到期提醒。"""

# 每个入口最近一次核查的结果与配置指纹，用于只重查发生变化的入口。
_CACHE = {}

_CERT_FIELDS = ("days_left", "protocols", "ciphers", "redirect", "chain_complete")


def _check_endpoint(host, endpoint, config, within):
    """给单个入口下一条结论，列出该入口的全部弱项。"""
    protocols = config["protocols"]
    ciphers = config["ciphers"]
    days_left = config["days_left"]
    chain_ok = config["chain_complete"]
    redirect = config["redirect"]

    weak = []
    weak.extend(rules.protocol_items(host, endpoint, protocols))
    weak.extend(rules.cipher_items(host, endpoint, ciphers))
    weak.extend(rules.certificate_items(host, endpoint, days_left, chain_ok))
    weak.sort(key=rules.item_sort_key)

    worst_cipher = None
    for suite in ciphers:
        severity = rules.cipher_severity(suite)[0]
        if severity is None:
            continue
        if worst_cipher is None or \
                rules.severity_rank(severity) > rules.severity_rank(worst_cipher["severity"]):
            worst_cipher = {"cipher": suite, "severity": severity}

    status = rules.cert_status(days_left, within)
    redirect_ok = bool(redirect)
    return {
        "host": host,
        "endpoint": endpoint,
        "address": config["address"],
        "port": config["port"],
        "protocols": list(protocols),
        "ciphers": list(ciphers),
        "protocol": rules.weakest_protocol(protocols),
        "strongest_protocol": rules.strongest_protocol(protocols),
        "cipher": worst_cipher["cipher"] if worst_cipher else None,
        "certificate": {
            "days_left": days_left,
            "status": status,
            "expire_soon": status in ("expiring", "expired"),
            "expired": status == "expired",
            "chain": list(config["chain"]),
            "chain_complete": chain_ok,
            "chain_issues": [] if chain_ok else ["签发链不完整，缺少中间证书"],
        },
        "redirect": redirect,
        "redirect_ok": redirect_ok,
        "weak": weak,
        "weak_count": len(weak),
        "worst": rules.worst_severity(weak),
        "verdict": rules.verdict(weak, redirect_ok),
        "notes": [] if redirect_ok else ["未配置 HTTP 到 HTTPS 的重定向"],
    }


def _host_payload(host, results, within):
    """把一个域名下各入口的结论整理成域名视图。"""
    # 域名视图按入口排列，同一入口内按严重程度排。
    endpoint_order = {name: index for index, name in enumerate(results)}
    items = [results[name] for name in results]
    weak = []
    for name in results:
        # 每个入口内部已按严重程度排好，这里保持入口内顺序不变。
        weak.extend(results[name]["weak"])
    weak.sort(key=lambda item: endpoint_order[item["endpoint"]])

    weak_by_endpoint = {}
    for name in results:
        if results[name]["weak"]:
            weak_by_endpoint[name] = results[name]["weak"]

    expiring = [
        {"endpoint": name, "days_left": results[name]["certificate"]["days_left"],
         "status": results[name]["certificate"]["status"]}
        for name in results if results[name]["certificate"]["expire_soon"]
    ]
    return {
        "host": host,
        "endpoint_count": len(items),
        "endpoints": items,
        "weak": weak,
        "weak_count": len(weak),
        "weak_by_endpoint": weak_by_endpoint,
        "expiring": expiring,
        "redirect_ok": all(result["redirect_ok"] for result in items),
        "verdict": rules.worst_verdict([result["verdict"] for result in items]),
        "within": within,
    }


def reset():
    """恢复初始入口登记，并清空核查缓存和整改记录。"""
    _CACHE.clear()
    inventory.reset()


def _remember(host, results, within):
    """缓存入口结论，只覆盖本次核查过的入口，顺带清掉已注销入口的缓存。"""
    live = set(inventory.endpoints(host))
    cache = {name: entry for name, entry in _CACHE.get(host, {}).items() if name in live}
    for name, result in results.items():
        cache[name] = {
            "fingerprint": inventory.fingerprint_of(inventory.get_endpoint(host, name)),
            "within": within,
            "result": result,
        }
    _CACHE[host] = cache


def check(host, endpoint=None, within=REMIND_DAYS, remember=True):
    """核查一个域名的全部入口（或指定入口），逐入口给出结论。

    返回入口清单、每个入口的证书状态、协议与套件、弱项和到期提醒。
    未知域名抛 UnknownHostError，未知入口抛 UnknownEndpointError。
    """
    if not inventory.has_host(host):
        raise UnknownHostError(f"unknown host: {host}")
    names = inventory.endpoints(host)
    if endpoint is not None:
        if endpoint not in names:
            raise UnknownEndpointError(f"unknown endpoint: {host}/{endpoint}")
        names = [endpoint]
    results = {}
    for name in names:
        config = inventory.get_endpoint(host, name)
        results[name] = _check_endpoint(host, name, config, within)
    if remember:
        _remember(host, results, within)
    return _host_payload(host, results, within)


def recheck(host=None, within=REMIND_DAYS):
    """整改后只重查配置发生变化的入口，未变化的直接复用上次结论。

    传 host 只处理该域名；不传则处理全部域名。从未查过的入口按变化处理；
    配置未变但提醒窗口变了（within 不同）的入口也重查，避免沿用旧窗口的结论。
    """
    targets = [host] if host is not None else inventory.hosts()
    if host is not None and not inventory.has_host(host):
        raise UnknownHostError(f"unknown host: {host}")
    checked = []
    skipped = []
    hosts_payload = []
    for current_host in targets:
        results = {}
        cached = _CACHE.get(current_host, {})
        for name in inventory.endpoints(current_host):
            fingerprint = inventory.fingerprint_of(
                inventory.get_endpoint(current_host, name))
            hit = cached.get(name)
            if (hit is not None and hit["fingerprint"] == fingerprint
                    and hit["within"] == within):
                results[name] = hit["result"]
                skipped.append(f"{current_host}/{name}")
            else:
                config = inventory.get_endpoint(current_host, name)
                results[name] = _check_endpoint(current_host, name, config, within)
                checked.append(f"{current_host}/{name}")
        _remember(current_host, results, within)
        hosts_payload.append(_host_payload(current_host, results, within))
    return {
        "checked": checked,
        "skipped": skipped,
        "hosts": hosts_payload,
        "changed": len(checked),
        "unchanged": len(skipped),
    }


_FIELD_LABEL = {
    "days_left": "证书剩余天数",
    "protocols": "开放协议版本",
    "ciphers": "开放加密套件",
    "redirect": "HTTPS 重定向",
    "chain_complete": "签发链完整性",
}

_FIELD_WORSE = {
    # 返回 True 表示 a 比 b 更弱（更该整改）。
    "days_left": lambda a, b: a < b,
    "protocols": lambda a, b: _has_weaker_protocol(a, b),
    "ciphers": lambda a, b: _has_weaker_cipher(a, b),
    "redirect": lambda a, b: bool(a) is False and bool(b) is True,
    "chain_complete": lambda a, b: a is False and b is True,
}


def _has_weaker_protocol(a, b):
    """a 里出现了 b 中没有的弱协议就算更弱。"""
    return any(protocol in rules.PROTOCOL_RULES and protocol not in b for protocol in a)


def _has_weaker_cipher(a, b):
    return any(rules.cipher_severity(cipher)[0] is not None and cipher not in b
               for cipher in a)


def compare(host):
    """对比同一域名下多个入口的配置差异。

    每个字段给出各入口取值、是否有差异、偏离多数且更弱的入口；
    `deviations` 只收“配置跟大多数不一致、而且更弱”的入口，便于直接整改。
    """
    if not inventory.has_host(host):
        raise UnknownHostError(f"unknown host: {host}")
    names = inventory.endpoints(host)
    configs = {name: inventory.get_endpoint(host, name) for name in names}
    fields = {}
    divergent = []
    deviations = {}
    for field in _CERT_FIELDS:
        per_endpoint = {}
        for name in names:
            value = configs[name][field]
            if field in ("protocols", "ciphers"):
                value = sorted(value)
            per_endpoint[name] = value
        distinct = []
        for value in per_endpoint.values():
            if value not in distinct:
                distinct.append(value)
        modal, modal_count = _modal(per_endpoint)
        has_majority = modal_count > 1
        worse = _FIELD_WORSE[field]
        rows = {}
        for name in names:
            value = per_endpoint[name]
            differs = has_majority and value != modal
            rows[name] = {
                "value": value,
                "differs_from_majority": differs,
                "weaker_than_majority": differs and worse(value, modal),
            }
        groups = [
            {"value": value, "endpoints": [n for n in names if per_endpoint[n] == value]}
            for value in distinct
        ]
        weakest = None
        for name in names:
            if weakest is None or worse(per_endpoint[name], per_endpoint[weakest]):
                weakest = name
        consistent = len(distinct) == 1
        fields[field] = {
            "label": _FIELD_LABEL[field],
            "endpoints": rows,
            "groups": groups,
            "consistent": consistent,
            "modal_count": modal_count,
            "weakest_endpoint": None if consistent else weakest,
        }
        if not consistent:
            divergent.append(field)
            for name in names:
                if rows[name]["weaker_than_majority"]:
                    deviations.setdefault(name, []).append(field)

    weakest_endpoint = None
    weakest_severity = None
    weakest_key = None
    for name in names:
        items = _check_endpoint(host, name, configs[name], REMIND_DAYS)["weak"]
        severity = rules.worst_severity(items)
        rank = rules.severity_rank(severity)
        key = (rank, sum(1 for item in items
                         if rules.severity_rank(item["severity"]) == rank),
               len(items))
        if items and (weakest_key is None or key > weakest_key):
            weakest_key = key
            weakest_endpoint = name
            weakest_severity = severity

    return {
        "host": host,
        "endpoints": names,
        "fields": fields,
        "divergent": divergent,
        "deviations": deviations,
        "consistent": not divergent,
        "weakest_endpoint": weakest_endpoint,
        "weakest_severity": weakest_severity,
    }


def _modal(per_endpoint):
    """出现次数最多的取值及次数；票数相同取登记顺序靠前者。"""
    counts = {}
    order = []
    for value in per_endpoint.values():
        key = repr(value)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
    best = max(order, key=lambda key: (counts[key], -order.index(key)))
    value = next(v for v in per_endpoint.values() if repr(v) == best)
    return value, counts[best]


def _reminders(rows, within):
    """把各域名核查结果里的到期入口汇总成提醒视图，按剩余天数升序。"""
    reminders = []
    by_host = {}
    for row in rows:
        for item in row["expiring"]:
            reminders.append(dict(item, host=row["host"]))
            names = by_host.setdefault(row["host"], [])
            if item["endpoint"] not in names:
                names.append(item["endpoint"])
    reminders.sort(key=lambda item: (item["days_left"], item["host"]))
    return {"within": within, "hosts": sorted(by_host), "endpoints": reminders,
            "by_host": by_host}


def expiring(within=REMIND_DAYS):
    """按域名列出剩余有效期不超过给定天数的入口，便于安排更换。"""
    rows = [check(host, within=within, remember=False) for host in inventory.hosts()]
    return _reminders(rows, within)


def report(within=REMIND_DAYS):
    """汇总全部域名的入口清单、弱配置、到期提醒和整改状态。"""
    rows = [check(host, within=within) for host in inventory.hosts()]
    weak = []
    for row in rows:
        weak.extend(row["weak"])
    weak.sort(key=lambda item: (
        -rules.severity_rank(item["severity"]),
        rules.CATEGORY_ORDER.get(item["category"], 9),
        item["host"],
        inventory.endpoints(item["host"]).index(item["endpoint"]),
    ))

    by_category = {category: [] for category in rules.CATEGORY_ORDER}
    by_severity = {level: [] for level in rules.SEVERITY_ORDER}
    for item in weak:
        by_category[item["category"]].append(item)
        by_severity[item["severity"]].append(item)

    weak_by_host = {row["host"]: row["weak_by_endpoint"] for row in rows
                    if row["weak_by_endpoint"]}
    verdicts = {level: [] for level in ("ok", "warn", "fail")}
    for row in rows:
        verdicts[row["verdict"]].append(row["host"])

    return {
        "hosts": [row["host"] for row in rows],
        "endpoints": sum(row["endpoint_count"] for row in rows),
        "weak": weak,
        "weak_count": len(weak),
        "weak_by_category": by_category,
        "weak_by_severity": by_severity,
        "weak_by_host": weak_by_host,
        "expiring": _reminders(rows, within),
        "remind_days": within,
        "verdict": verdicts,
        "changes": inventory.changes(),
    }
