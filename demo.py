from symrecover import recover, report

BINARY = {
    "name": "routerd",
    "version": "1.0",
    "functions": [
        {"address": "0804a100", "calls": ["0804a200"], "strings": ["admin"]},
        {"address": "0804a200", "calls": ["0804a100"], "strings": ["/tmp/key"]},
    ],
}

result = recover(BINARY)
print("recovered", result["recovered"])
print(result["names"][0])
print(report())

# TODO: 第二段演示（实现后补）
