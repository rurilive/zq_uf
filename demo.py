from pprint import pprint

from corpusmin import add, minimize, report, restore

CORPUS = [
    # pdf：parser 区域重复，xref 只有 xref-seed 覆盖。
    {"id": "seed-01", "project": "pdf", "size": 120, "covers": ["parser.c:1-40", "xref.c:1-9"]},
    {"id": "seed-02", "project": "pdf", "size": 80, "covers": ["parser.c:1-40"]},
    {"id": "seed-03", "project": "pdf", "size": 60, "covers": ["parser.c:1-40", "xref.c:1-9"]},
    {"id": "seed-04", "project": "pdf", "size": 10, "covers": ["parser.c:41-60"]},
    # img：png 区域，只有一份语料。
    {"id": "seed-05", "project": "img", "size": 40, "covers": ["png.c:1-9"]},
]

result = minimize(CORPUS)
print("保留:", result["kept"], "丢弃:", result["dropped"])
print("覆盖: %d -> %d（复跑一致: %s）" % (
    result["coverage_before"], result["coverage_after"], result["coverage_preserved"]))
print("只有一份语料撑着的区域:", result["solo_regions"])
print("被丢弃语料的去向（按项目）:")
pprint({item_id: {k: v for k, v in record.items() if k != "entry"}
        for item_id, record in result["archive"].items()})

print("\n按项目查看:")
for project, detail in report()["projects"].items():
    print("  %-3s 保留 %d 丢弃 %d 覆盖区域 %d" % (
        project, detail["kept_count"], detail["dropped_count"], detail["coverage"]))

print("\n取回 seed-02:", restore("seed-02")["restored"])
print("pdf 覆盖区域:", sorted(report("pdf")["coverage_by_region"]))
print("pdf 轮次:", [(r["round"], r["action"]) for r in report("pdf")["rounds"]])

print("\n新增语料:", add([{"id": "seed-06", "project": "img", "size": 20,
                          "covers": ["png.c:1-9", "png.c:10-20"]}]))
