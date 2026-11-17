from dirprobe import load, probe, report, update

WORDS = ["admin", "backup.zip", "login", "static", "robots.txt", "images"]
RESPONSES = {
    "admin": {"status": 200},
    "backup.zip": {"status": 200},
    "login": {"status": 403},
    "static": {"status": 429, "body": "too many requests"},
    "robots.txt": {"status": 200},
    "images": {"status": 301},
}

print("字典条数", load("shop", WORDS, RESPONSES)["words"])
out = probe("shop")
print("命中路径", [hit["path"] for hit in out["hits"]])
print("拦截次数", out["blocked"], "当前并发", out["concurrency"])
print("续跑位置", out["resume"], "报告命中", report("shop")["hits"])
print("命中归类", report("shop")["groups"])
print("被拦截批次", report("shop")["blocked_paths"])

# 第二段：目标整体拒绝访问，停本轮保留进度，恢复后续跑
WORDS2 = ["a", "b", "x", "y", "z", "d"]
RESPONSES2 = {
    "a": {"status": 200},
    "b": {"status": 404},
    "x": {"error": "connection refused"},
    "y": {"error": "connection refused"},
    "z": {"error": "connection refused"},
    "d": {"status": 200},
}
load("api", WORDS2, RESPONSES2)
out = probe("api")
print("被拒停止", out["stopped"], "续跑位置", out["resume"],
      "已完成", report("api")["done"], "/", report("api")["total"])
update("api", {"x": {"status": 404}, "y": {"status": 404}, "z": {"status": 404}})
out = probe("api")
print("续跑后命中", [hit["path"] for hit in out["hits"]],
      "续跑位置", out["resume"])
