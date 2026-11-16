import json

from hostbaseline import check, report, reset

reset()

# 第一段：按系统核查，Windows 上 ssh_root_login 自动判为不适用
print(check({"host": "lnx-01", "os": "linux", "os_version": "8",
             "items": {"firewall": "off", "antivirus": "on", "ssh_root_login": "no"}}))
print(check({"host": "win-01", "os": "windows", "os_version": "11",
             "items": {"firewall": "on", "antivirus": "on",
                       "bitlocker": "on", "guest_account": "disabled"}}))
print(json.dumps(report(), ensure_ascii=False, indent=2))

# 第二段：整改后只复验未通过项，首次与复验结果并排对比，整改清单可导出
print(check({"host": "lnx-01", "items": {"firewall": "on"}}, recheck=True))
print(json.dumps(report(host="lnx-01"), ensure_ascii=False, indent=2))
print("整改清单：", json.dumps(report()["remediation"], ensure_ascii=False))
