from vulnticket import report, update

print(update({"id": "T-1", "vuln": "V-9", "project": "shop"}))
print(update({"id": "T-1", "vuln": "V-9", "project": "shop", "status": "closed", "owner": "chen"}))
print(report("shop")["tickets"])

# TODO: 第二段演示（实现后补）
