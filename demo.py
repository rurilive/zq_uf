"""两段演示：基础能力 + 还原/行为/分组/白名单。

运行：python3 demo.py
"""

from datetime import datetime, timedelta, timezone

from webshell import report, reset, scan, whitelist

print("== 第一段：基础扫描（兼容旧调用，value 直接传字符串） ==")
files = {
    "upload/avatar.php": "<?php\necho eval($_POST['c']);\n",
    "upload/loader.php": "<?php\n$f = 'ev'.'al';\n$f($_POST['c']);\n",
    "upload/util.php": "<?php\nreturn 1;\n",
}
print("命中文件:", scan(files)["files"])
print("按文件计数:", report()["counts"])
print("白名单:", whitelist("upload/avatar.php"))
print("加白后同目录重扫:", scan(files)["files"])

reset()

print("\n== 第二段：还原、行为信号与分组 ==")
created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
access = [
    {"method": "POST", "query": "", "post": "cmd=whoami", "status": 200},
    {"method": "GET", "query": "x=1", "status": 404},
]
samples = {
    # base64 包裹的 eval，关键字匹配完全看不到
    "upload/b64.php": "<?php\n$p = 'ZXZhbCgkX1BPU1RbJ2MnXSk7';\neval(base64_decode($p));\n",
    # 同目录下结构相同的两个变体，应被分到一组
    "upload/a1.php": "<?php\n$a = 'ev'.'al';\n$a($_REQUEST['x']);\n",
    "upload/a2.php": "<?php\n$b = 'ev'.'al';\n$b($_REQUEST['y']);\n",
    # 正常业务：注释和变量名里带 eval 字样，不应上报
    "upload/biz.php": "<?php\n// eval() usage is forbidden in reviews\n"
    "$evaluation = $_GET['name'];\necho htmlspecialchars($evaluation);\n",
    # 带创建时间和访问日志的样本
    "upload/recent.php": {
        "content": "<?php\nsystem($_POST['cmd']);\n",
        "created_at": created,
        "access": access,
    },
}
result = scan(samples)
for finding in result["findings"]:
    top = finding["matches"][0]
    print(
        f"{finding['file']}  score={finding['score']} risk={finding['risk']} "
        f"count={finding['count']} group={finding['group_id']}"
    )
    print(f"  首条命中 L{top['line']}: {top['rule']} -> {top['target']}")
    for record in finding["restoration"][:2]:
        print(f"  还原: {record['technique']}: {record['to'][:60]}")

print("\n分组:", [group["files"] for group in result["groups"]])
print(
    "白名单只作用单文件，biz 之外同目录文件照常扫描:",
    scan(
        {
            "upload/biz.php": samples["upload/biz.php"],
            "upload/other.php": "<?php eval($_POST['z']);\n",
        }
    )["files"],
)

body = report()
print("\n按目录结论:")
for directory, bucket in body["by_directory"].items():
    print(f"  {directory}: {len(bucket['files'])} 个文件, 高危 {bucket['high_risk']}")
    for entry in bucket["files"]:
        print(
            f"    - {entry['file']} 风险={entry['risk']} 次数={entry['count']} "
            f"首行={entry['first_line']} 组={entry['group_id']}"
        )
