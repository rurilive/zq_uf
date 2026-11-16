from dirprobe import load, probe, report

WORDS = ["admin", "backup.zip", "login", "static"]
RESPONSES = {
    "admin": {"status": 200},
    "backup.zip": {"status": 200},
    "login": {"status": 403},
    "static": {"status": 429},
}

print("字典条数", load("shop", WORDS, RESPONSES)["words"])
out = probe("shop")
print("命中路径", [hit["path"] for hit in out["hits"]])
print("拦截次数", out["blocked"], "当前并发", out["concurrency"])
print("续跑位置", out["resume"], "报告命中", report("shop")["hits"])

# TODO: 第二段演示（实现后补）
