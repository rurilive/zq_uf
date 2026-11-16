from exploitchain import report, results, verify

CHAIN = {
    "id": "chain-7",
    "source": "zhang",
    "steps": [
        {"name": "auth bypass", "pre": ["login"], "input": "/admin", "expect": 200, "actual": 200},
        {"name": "file read", "pre": ["auth bypass"], "input": "/etc/passwd", "expect": "root:", "actual": "root:x:0:0"},
        {"name": "exfil", "pre": ["file read"], "input": "10.0.0.5:9000", "expect": True, "actual": False},
    ],
}

print(verify(CHAIN)["verdict"])
print(results("chain-7"))
print(report())

# TODO: 第二段演示（实现后补）
