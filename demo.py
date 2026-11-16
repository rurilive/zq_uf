from hostbaseline import check, report

print(check({"host": "lnx-01", "os": "linux", "items": {"firewall": "on", "antivirus": "on", "ssh_root_login": "no"}}))
print(check({"host": "win-01", "os": "windows", "items": {"firewall": "on", "antivirus": "on"}}))
print(report())

# TODO: 第二段演示（实现后补）
