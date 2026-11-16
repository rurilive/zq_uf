"""演示：形态 + 上下文识别、同值合并、使用痕迹、白名单与处置流。

运行：python3 demo.py
所有输入都是内存里的字符串映射，扫描过程不读磁盘、无外部依赖。
"""

from keyleak import report, reset, scan, triage, whitelist

PATHS = {
    # 弱口令赋值：形态不随机，全靠 password 上下文，低可信，进人工队列。
    "web/app.py": 'password = "frontdoor-lock-27"\ntoken = "samevalue"\n',
    # 同一密钥出现在第二个仓库的配置里 —— 合并成一条，位置区分 code/config。
    "ops/deploy.yml": "api_key = samevalue\n",
    # 高熵字符串，即使变量名没有密钥字样也能被形态规则抓到。
    "web/fixture.py": 'blob = "aB3xK9mQ2pL7vN4rT8wZ"\n',
    # 已知前缀（AWS），高可信；并在业务代码里被调用 → 仍在使用。
    "ops/conf.yaml": 'aws_key: "AKIAIOSFODNN7EXAMPLE"\n',
    "web/client.py": 'client = Client("AKIAIOSFODNN7EXAMPLE")\n',
    # 构建产物里的副本，位置类型标成 build，避免删源码时漏掉打包泄露。
    "web/dist/bundle.js": 't="AKIAIOSFODNN7EXAMPLE"\n',
    # 占位符与示例值属于误报，不应进结果。
    "web/settings.env": 'export TOKEN="${TOKEN}"\npassword = "changeme"\n',
}


def main():
    reset()
    result = scan(PATHS)

    print("== scan() 合并后的密钥条目 ==")
    for entry in result["entries"]:
        print(
            f"[{entry['confidence']:6}] {entry['type']:20} {entry['masked']:14} "
            f"status={entry['status']:9} in_use={entry['in_use']}"
        )
        for loc in entry["locations"]:
            print(f"    - {loc['kind']:6} {loc['file']}:{loc['line']}")

    print("\n== 按仓库分组的文件计数 ==")
    print(result["repos"])

    print("\n== report() 处置看板 ==")
    body = report()
    print(
        f"total={body['total']} open={body['open']} in_use={body['in_use']} "
        f"resolved={body['resolved']} whitelisted={body['whitelisted']} "
        f"duplicates={body['duplicates']}"
    )
    print("repos:", body["repos"])

    # 第二段：把确认的误报加白，再记录一条真实处置。
    print("\n== 白名单 + 处置 ==")
    whitelist(value="frontdoor-lock-27")
    aws_id = next(e["id"] for e in report()["entries"] if e["type"] == "aws_access_key_id")
    triage(aws_id, handler="alice", method="rotated", note="迁到 KMS，旧密钥已轮换")
    body = report()
    print(
        f"whitelisted={body['whitelisted']} resolved={body['resolved']} "
        f"open={body['open']} in_use={body['in_use']}"
    )


if __name__ == "__main__":
    main()
