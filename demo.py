import os
import tempfile

from filerouter import retry_pending, route

# 第一段：默认规则（按后缀）
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "a.log")
    open(src, "w").write("x")
    print(route(src, os.path.join(tmp, "out")))

# 第二段：自定义规则分发一批混合文件
RULES = [
    {"name": "error-log", "suffix": "log", "contains": "ERROR", "priority": 10},
    {"name": "log", "suffix": "log"},
    {"name": "big-archive", "suffix": ["zip", "gz"], "min_size": 4},
    {"name": "report", "prefix": "report_", "suffix": "csv"},
]

with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "out")
    inbox = os.path.join(tmp, "collector-a")
    os.makedirs(inbox)

    batch = {
        "app.log": "INFO started\nERROR disk full\n",
        "access.log": "GET / 200\n",
        "dump.zip": "PK\x03\x04payload",
        "report_2026.csv": "date,uv\n",
        "unknown.bin": "\x00\x01",
    }
    for name, content in batch.items():
        with open(os.path.join(inbox, name), "w") as f:
            f.write(content)

    print("== 首次分发 ==")
    for name in sorted(batch):
        print(route(os.path.join(inbox, name), out, rules=RULES))

    print("== 重名加序号 ==")
    again = os.path.join(inbox, "app.log")
    with open(again, "w") as f:
        f.write("ERROR again\n")
    print(route(again, out, rules=RULES))

    print("== 待处理 ==")
    for name in sorted(os.listdir(os.path.join(out, "_pending"))):
        print(name)

    print("== 补规则后重跑（只处理待处理目录）==")
    for name, result in retry_pending(out, rules=RULES + [{"name": "bin", "suffix": "bin"}]):
        print(name, "->", result)
