"""keyleak 端到端演示：扫描 -> 合并 -> 白名单 -> 处置 -> 按仓库汇总。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keyleak import add_whitelist, report, resolve, scan

PATHS = {
    # 低强度关键字命中 + 高熵字符串
    "web/app.py": (
        'password = "hunter2"\n'
        'token = "aB3xK9mQ2pL7vN4rT8wZ"\n'
        'header = "aB3xK9mQ2pL7vN4rT8wZ"\n'  # 同一值再次出现：调用痕迹
    ),
    # 同一密钥在另一个仓库：合并成一条
    "ops/deploy.yml": "api_key = samevalue\n",
    "web/config.py": 'api_key = "samevalue"\n',
    # 已知前缀，高可信
    "ops/aws.sh": 'export AWS_KEY="AKIA1234567890ABCDEF"\n',
    # 构建产物中的密钥：标记为仍在使用
    "dist/bundle.min.js": 't="aB3xK9mQ2pL7vN4rT8wZ";\n',
    # 占位符/模板：误报过滤
    "web/tmpl.py": 'password = "${SERVICE_PASSWORD}"\n',
}

result = scan(PATHS)
print("== 合并后的密钥条目 ==")
for entry in result["entries"]:
    print(
        entry["entry_id"], entry["type"], entry["confidence"],
        entry["masked"], "in_use=%s" % entry["in_use"],
        "locations=%d" % len(entry["locations"]),
    )
print("== 按仓库分组（分派用） ==", result["repo_groups"])
print("== 汇总 ==", report())

# 第二段：误报白名单与处置闭环
add_whitelist(["hunter2"])                       # 安全同学确认的误报
merged_id = [e for e in scan(PATHS)["entries"]
             if e["value"] == "samevalue"][0]["entry_id"]
resolve(merged_id, handled_by="alice", method="rotated")  # 记录处置
body = report()
print("== 处置后汇总 ==")
print("open:", body["open"], "resolved:", body["resolved"],
      "whitelisted:", body["whitelisted"], "in_use:", body["in_use"])
