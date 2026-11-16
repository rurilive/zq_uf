"""证书核查，当前每个域名只看主入口的一项配置。"""

_HOSTS = {
    "shop.example.com": {
        "address": "10.1.0.11",
        "days_left": 12,
        "protocol": "TLSv1.0",
        "cipher": "RC4-SHA",
        "redirect": False,
    },
    "api.example.com": {
        "address": "10.1.0.21",
        "days_left": 210,
        "protocol": "TLSv1.2",
        "cipher": "AES128-SHA",
        "redirect": True,
    },
    "cdn.example.com": {
        "address": "10.1.0.31",
        "days_left": 45,
        "protocol": "TLSv1.2",
        "cipher": "AES256-GCM",
        "redirect": True,
    },
}

_WEAK_PROTOCOL = {"SSLv3", "TLSv1.0", "TLSv1.1"}
_WEAK_CIPHER = {"RC4-SHA", "AES128-SHA", "DES-CBC3-SHA"}


def check(host):
    """给单个域名的主入口下一条结论。"""
    if host not in _HOSTS:
        raise ValueError("unknown host")
    entry = _HOSTS[host]
    return {
        "host": host,
        "address": entry["address"],
        "days_left": entry["days_left"],
        "expire_soon": entry["days_left"] <= 30,
        "protocol": entry["protocol"],
        "cipher": entry["cipher"],
        "weak": entry["protocol"] in _WEAK_PROTOCOL or entry["cipher"] in _WEAK_CIPHER,
    }


def expiring(within=30):
    """列出剩余天数不超过给定值的域名。"""
    return {"within": within, "hosts": [h for h in sorted(_HOSTS) if _HOSTS[h]["days_left"] <= within]}


def report():
    """汇总域名清单、弱配置和到期提醒。"""
    weak = []
    soon = []
    for host in sorted(_HOSTS):
        row = check(host)
        if row["weak"]:
            weak.append(host)
        if row["expire_soon"]:
            soon.append(host)
    return {
        "hosts": sorted(_HOSTS),
        "weak": weak,
        "expiring": soon,
        "endpoints": len(_HOSTS),
    }
