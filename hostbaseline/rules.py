"""基线检查项规则库，按系统和版本分类。

每条规则声明：
- name: 检查项名称
- expected: 期望值
- systems: 适用系统列表；os 为系统类型，versions 省略表示该系统全版本适用。

主机未提供 os_version 时不做版本排除（无法确认版本即视为适用）；
版本明确且不在适用列表内时，该检查项判为不适用。
"""

RULES = [
    {
        "name": "firewall",
        "expected": "on",
        "systems": [
            {"os": "linux"},
            {"os": "windows"},
        ],
    },
    {
        "name": "antivirus",
        "expected": "on",
        "systems": [
            {"os": "linux"},
            {"os": "windows"},
        ],
    },
    {
        "name": "ssh_root_login",
        "expected": "no",
        "systems": [
            {"os": "linux", "versions": ["7", "8", "9"]},
        ],
    },
    {
        "name": "bitlocker",
        "expected": "on",
        "systems": [
            {"os": "windows", "versions": ["10", "11", "2019", "2022"]},
        ],
    },
    {
        "name": "guest_account",
        "expected": "disabled",
        "systems": [
            {"os": "windows"},
        ],
    },
]


def applies(rule, os_name, os_version=None):
    """判断规则是否适用于指定系统和版本。"""
    for entry in rule["systems"]:
        if entry["os"] != os_name:
            continue
        versions = entry.get("versions")
        if not versions or os_version is None or os_version in versions:
            return True
    return False
