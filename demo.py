from sqldetect import check, load, mark_false_positive, report

URL = "http://app.local/item"

# 每个参数可以登记多次尝试：正文、耗时、HTTP 状态都能表达。
RESPONSES = {
    # 报错型：命中 MySQL 特征
    "id": [
        {"payload": "'", "body": "You have an error in your SQL syntax near '''"},
        {"payload": "\"", "body": "You have an error in your SQL syntax near '\"'"},
    ],
    # 布尔盲注：真条件回到正常页，假条件回到空页
    "name": {
        "baseline": {"body": "<html>welcome back, list of 12 items</html>", "elapsed": 0.12},
        "true": {"payload": "' OR '1'='1", "body": "<html>welcome back, list of 12 items</html>", "elapsed": 0.13},
        "false": {"payload": "' OR '1'='2", "body": "<html>no results found</html>", "elapsed": 0.12},
    },
    # 时间盲注：SLEEP(5) 明显变慢
    "sort": {
        "baseline": {"body": "<html>sorted</html>", "elapsed": 0.2},
        "timed": {"payload": "1' AND SLEEP(5)--", "body": "<html>sorted</html>", "elapsed": 5.3},
    },
    # 页面本身就在报错，且基线也有：不算注入
    "page": {
        "baseline": {"body": "unknown column error in template"},
        "true": {"payload": "' OR '1'='1", "body": "unknown column error in template"},
        "false": {"payload": "' OR '1'='2", "body": "unknown column error in template"},
    },
    # 通用报错、认不出数据库：待确认
    "q": "SQL syntax issue near input",
    # 探测失败：超时/拒绝/协议不支持
    "slow": {"skip": "timeout"},
    "admin": {"status": 403, "body": "Forbidden"},
    "hook": {"skip": "协议不支持：ftp:// scheme"},
}

print("登记参数", load(URL, RESPONSES)["params"])

out = check(URL, ["id", "name", "sort", "page", "q", "slow", "admin", "hook", "missing"])
print("可疑参数", [item["param"] for item in out["findings"]])
for item in out["findings"]:
    print("  -", item["param"], item["kind"], "dbms=%s" % item["dbms"],
          "status=%s" % item["status"], "confidence=%.2f" % item["confidence"])
    print("    依据", item["evidence"])
    print("    请求", item["request"])
    print("    复现", " -> ".join(item["steps"]))

print("未覆盖", [(item["param"], item["reason"]) for item in out["uncovered"]])
print("待确认", out["pending"])

rep = report(URL)
print("接口结论数", rep["findings"], "确认", rep["confirmed"], "待确认", rep["pending"])

# 运营把 name 标成误报并写原因
mark_false_positive(URL, "name", "业务本身对空结果有不同模板，复测后确认为正常")
rep = report(URL)
print("误报", rep["false_positive_details"])
print("剩余确认结论", rep["confirmed"])
