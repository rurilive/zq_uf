import json

from hostbaseline import check, export_remediation, report


def show(title, payload):
    print(f"\n== {title} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# 首次核查：Windows 和 Linux 各自适用不同的检查项
linux = check({
    "host": "lnx-01",
    "os": "linux",
    "version": "9.2",
    "items": {"firewall": "off", "ssh_root_login": "no", "selinux": "enforcing"},
})
windows = check({
    "host": "win-01",
    "os": "windows",
    "version": "10.0",
    "items": {"firewall": "on", "antivirus": "on", "smb1": "on"},
})

show("linux 首次核查", linux)
show("windows 首次核查", windows)

# 整改后只复验未通过项
show("lnx-01 复验", check({
    "host": "lnx-01",
    "os": "linux",
    "version": "9.2",
    "items": {"firewall": "on", "ssh_root_login": "no", "selinux": "enforcing"},
}, recheck=True))

# 汇总：按系统、按检查项给结论，不适用项不计入通过率
summary = report()
show("汇总（按系统）", {
    "rate": summary["rate"],
    "by_os": summary["by_os"],
    "remediation": summary["remediation"],
})
show("按检查项 × 系统", summary["by_item"])

# 按终端查看，并导出整改清单
show("win-01 明细", report(host="win-01"))
export_remediation("/tmp/remediation.csv")
print("\n整改清单已导出: /tmp/remediation.csv")
