from encodepage import decode


CASES = [
    (
        "响应头声明 GBK",
        "中文页面".encode("gbk"),
        {"content-type": "text/html; charset=gbk"},
    ),
    (
        "无响应头，默认 UTF-8",
        "默认编码页面".encode("utf-8"),
        {},
    ),
    (
        "头缺失，meta 声明 GBK",
        '<html><head><meta charset="gbk"></head><body>老站点页面</body></html>'.encode("gbk"),
        {},
    ),
    (
        "头与 meta 都缺失，按字节分布打分",
        "纯字节打分识别为GBK".encode("gbk"),
        {},
    ),
    (
        "响应头声明与实际编码冲突",
        "声明UTF8实际GBK".encode("gbk"),
        {"content-type": "text/html; charset=utf-8"},
    ),
    (
        "页面混用两种编码",
        "开头正常UTF8文字".encode("utf-8") + "一段GBK内容".encode("gbk") + b" tail",
        {},
    ),
    (
        "所有编码均失败，单字节兜底",
        b"\xff" * 8 + b"\xfe\xfd",
        {},
    ),
]


for title, body, headers in CASES:
    result = decode(body, headers)
    print("=" * 60)
    print(title)
    print("文本:    %s" % str(result)[:40])
    print("编码:    %s" % result.encoding)
    print("置信度:  %.4f" % result.confidence)
    if result.warnings:
        for warning in result.warnings:
            print("告警:    %s" % warning)
    else:
        print("告警:    无")
