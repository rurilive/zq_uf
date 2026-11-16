from exploitchain import report, replay, results, verify

# 链条一：前两步打通，第三步外带失败 -> partial，断在 exfil
PARTIAL_CHAIN = {
    "id": "chain-7",
    "source": "zhang",
    "steps": [
        {"name": "auth bypass", "pre": ["login"], "input": "/admin", "expect": 200, "actual": 200},
        {"name": "file read", "pre": ["auth bypass"], "input": "/etc/passwd", "expect": "root:", "actual": "root:"},
        {"name": "exfil", "pre": ["file read"], "input": "10.0.0.5:9000", "expect": True, "actual": False},
    ],
}

# 链条二：第一步就被挡住 -> unusable，第二步因前置不满足被 blocked
BLOCKED_CHAIN = {
    "id": "chain-9",
    "source": "li",
    "steps": [
        {"name": "rce", "pre": ["exposure"], "input": "ping 127.0.0.1", "expect": "shell", "actual": "blocked by waf"},
        {"name": "takeover", "pre": ["rce"], "input": "crontab", "expect": True, "actual": True},
    ],
}

print("== chain-7 逐步验证 ==")
entry = verify(PARTIAL_CHAIN)
print("verdict:", entry["verdict"], "| failed_at:", entry["failed_at"],
      "| reached:", entry["reached_step"], "| source:", entry["source"])
for step in entry["steps"]:
    print("  %-12s %-7s input=%r response=%r elapsed=%sms reason=%s"
          % (step["step"], step["status"], step["input"], step["response"],
             step["elapsed_ms"], step["reason"]))
print("外部前置:", entry["preconditions"])

print("\n== 失败步骤单独重放（保留当时响应和耗时） ==")
print(replay("chain-7", "exfil"))

print("\n== chain-9 前置阻断 ==")
blocked = verify(BLOCKED_CHAIN)
print("verdict:", blocked["verdict"], "| failed_at:", blocked["failed_at"])
for step in blocked["steps"]:
    print("  %-10s %-7s pre=%s reason=%s"
          % (step["step"], step["status"], step["pre"], step["reason"]))

print("\n== 最近一次结果 ==")
print(results("chain-7")[0]["verdict"], results("chain-7")[0]["failed_at"])

print("\n== 汇总报告（三档 + 断裂点 + 提交人） ==")
print(report())
