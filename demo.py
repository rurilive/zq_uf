from assetmerge import merge, report, scan_list

ASSETS = [
    {"id": "a1", "host": "web1", "kind": "domain", "value": "www.example.com",
     "vulns": ["vuln-sqli"]},
    {"id": "a2", "host": "web1", "kind": "ip", "value": "10.0.0.8",
     "vulns": ["vuln-sqli"]},
    {"id": "a3", "host": "web1", "kind": "port", "value": "10.0.0.8:443",
     "vulns": ["vuln-xss"]},
]

out = merge(ASSETS)
print("记录数", out["count"])
print("别名数", out["alias_count"])
print("合并依据", out["basis"])
print("下发清单", scan_list())

# TODO: 第二段演示（实现后补）
