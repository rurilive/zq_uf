from auditlog import (
    append,
    checked_ranges,
    days,
    rerun,
    set_algo,
    tail,
    verify,
    verify_day,
)
from auditlog import chain

DAY1 = {"day": "2026-01-01", "ts": "2026-01-01T23:58:00", "action": "login",
        "user": "alice"}
DAY2 = {"day": "2026-01-02", "ts": "2026-01-02T00:01:00", "action": "read",
        "user": "bob"}
DAY2B = {"day": "2026-01-02", "ts": "2026-01-02T09:30:00", "action": "export",
         "user": "bob"}
DAY3 = {"day": "2026-01-03", "ts": "2026-01-03T08:00:00", "action": "logout",
        "user": "carol"}

print("== append 返回摘要值与衔接值 ==")
first = append(DAY1)
second = append(DAY2)
append(DAY2B)
append(DAY3)
print("seq", first["seq"], "link", repr(first["link"]))
print("seq", second["seq"], "day_start", second["day_start"],
      "link==prev.digest", second["link"] == first["digest"])
print("digest", first["digest"][:16], second["digest"][:16])
print("tail", tail()["seq"], tail()["digest"] == chain._records[-1]["digest"])

print("\n== 按天查看，跨天衔接值保留在第一天出口 ==")
for part in days():
    print(part["day"], part["start"], part["end"], "entry",
          (part["entry_link"] or "-")[:8])

print("\n== 只校验指定区间，可跨天 ==")
span = verify({"from_day": "2026-01-02", "to_day": "2026-01-03"})
print(span["checked"], "条，覆盖",
      [part["day"] for part in span["days"]])

print("\n== 中断后从断点续算 ==")
part1 = verify((0, 3), limit=2)
print("第一次 checked", part1["checked"], "resume", part1["resume"])
part2 = verify((0, 3), resume=part1["resume"])
print("续算 from", part2["start"], "to", part2["end"], "ok", part2["ok"])

print("\n== 摘要算法变更，旧链接仍可验证 ==")
set_algo("sha3_256")
tail_before = tail()["digest"]
new = append({"day": "2026-01-04", "ts": "2026-01-04T10:00:00",
              "action": "login", "user": "dave"})
print("新记录算法", new["algo"], "link 接得上", new["link"] == tail_before)
print("全链校验", verify()["ok"], "算法", verify()["algos"])

print("\n== 断链定位到具体记录，并给出前后两条和影响范围 ==")
chain._records[3]["record"]["action"] = "deleted"
result = verify()
print("ok", result["ok"], "broken", result["broken"],
      "区间", result["broken_range"])
detail = result["broken_detail"]
print("原因", detail["reason"], "时间", detail["time"])
for label, neighbor in (("前一条", detail["prev"]), ("后一条", detail["next"])):
    if neighbor is None:
        print(label, "无")
    else:
        print(label, neighbor["seq"], neighbor["time"], neighbor["day"])
print("影响", result["impact"])

print("\n== 只校验某一天，不必重算历史 ==")
one_day = verify_day("2026-01-02", expect_tail=days()[1]["exit_digest"])
print("2026-01-02", one_day["start"], one_day["end"], "ok", one_day["ok"],
      "锚点来源", one_day["anchor_source"])

print("\n== 历史区间可单独重跑 ==")
print("已校验区间", [(item["range"], item["ok"]) for item in checked_ranges()])
print("重跑 0..1", rerun((0, 1))["ok"])
