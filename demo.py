from exploitchain import replay, report, results, verify

CHAIN = {
    "id": "chain-7",
    "source": "zhang",
    "steps": [
        {"name": "auth bypass", "pre": ["login"], "input": "/admin", "expect": 200, "actual": 200},
        {"name": "file read", "pre": ["auth bypass"], "input": "/etc/passwd", "expect": "root:", "actual": "root:x:0:0"},
        {"name": "exfil", "pre": ["file read"], "input": "10.0.0.5:9000", "expect": True, "actual": False},
    ],
}

entry = verify(CHAIN)
print(entry["verdict"])      # partial：前两步打通，卡在 exfil
print(entry["failed_at"])    # exfil
print(entry["reached"])      # 2
print(results("chain-7")[0]["source"])  # zhang，来源落到人

# 第二段：单步重放 + 全通链 + 汇总报告
print(replay("chain-7", "exfil"))  # 用记录的入参单独重放失败步骤

OK_CHAIN = {
    "id": "chain-8",
    "source": "li",
    "steps": [
        {"name": "scan", "pre": [], "input": "10.0.0.0/24", "expect": "up", "actual": "up"},
        {"name": "login", "pre": ["scan"], "input": "admin", "expect": True, "actual": True},
    ],
}
print(verify(OK_CHAIN)["verdict"])  # usable

print(report())
