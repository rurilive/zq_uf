from auditlog import append, boundary, days, set_algorithm, tail, verify

DAY1 = {"day": "day-01", "time": "09:00", "action": "login", "user": "alice"}
DAY2 = {"day": "day-02", "time": "10:00", "action": "read", "user": "bob"}

first = append(DAY1)
second = append(DAY2)
print("seq", first["seq"], "link", repr(first["link"]))
print("digest", first["digest"], second["digest"])
print("tail", tail()["seq"], tail()["digest"] == second["digest"])
print("span", verify((1, 1)))

# 按天切分：第二天的 link 就是第一天的尾摘要，跨天衔接不断
print("boundary", boundary("day-02"))
print("days", [(d["day"], d["link"] == first["digest"]) for d in days()])

# 分段校验 + 断点续算：每次只算 1 条，令牌接着往下走
patch = verify(("day-01", "day-02"), limit=1)
while patch["resume"] is not None:
    print("patch", patch["checked"], patch["end"], patch["complete"])
    patch = verify(resume=patch["resume"], limit=1)
print("done", patch["ok"], patch["checked_total"])

# 算法迁移：旧区间按记录上的算法验证
set_algorithm("sha1")
third = append({"day": "day-02", "time": "11:00", "action": "logout", "user": "bob"})
print("algorithms", [r["algorithm"] for r in (first, second, third)])
print("old range", verify((0, 1))["ok"], "new range", verify((2, 2))["ok"])

# 断链定位：改掉一条内容，报告前后两条记录和时间
from auditlog import chain

chain._records[1]["record"]["action"] = "deleted"
result = verify()
info = result["break"]
print("broken", result["broken"], result["reason"], result["broken_time"])
print("prev", info["prev"]["seq"], info["prev"]["time"],
      "next", info["next"]["seq"], info["next"]["time"])
print("impact", result["impact"]["start"], result["impact"]["end"],
      result["impact"]["count"])
