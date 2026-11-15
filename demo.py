"""用一个商户跨三个站点、主键各不相同的例子演示 rowfold。"""

import os
import tempfile

from rowfold import RowFold

# 同一家商户在三个站点的主键不同；site1 的记录先到但字段很少。
ROWS = [
    {
        "key": "site1-100", "source": "site1", "name": "老王牛肉面",
        "city": "上海", "amount": 120, "updated_at": "2026-01-02",
    },
    {
        "key": "shop-77", "source": "site2", "name": "老王牛肉面",
        "city": "上海", "phone": "021-8888", "contact": "王强",
        "amount": 120, "updated_at": "2026-03-05",
    },
    {
        "key": "site3-9", "source": "site3", "name": "小李烧烤",
        "city": "北京", "amount": 80, "updated_at": "2026-02-01",
    },
]

CONFIG = {
    "key_fields": ("name", "city", "phone", "contact"),
    "completeness_fields": ("name", "city", "phone", "contact"),
}


def main():
    fold = RowFold(**CONFIG)

    print("== 1. 在 merchant 业务域试运行（不落库） ==")
    trial = fold.dry_run(ROWS, domain="merchant")
    print(f"行数 {len(ROWS)} -> 主记录 {len(trial['rows'])}，合并 {trial['folded']} 条")
    print("金额:", trial["amounts"])
    print("核对项:", trial["checks"])
    try:
        fold.explain("shop-77")
    except KeyError:
        print("试运行期间查不到记录（符合预期）")

    print("\n== 2. 金额核对通过后推广 ==")
    promoted = fold.promote("merchant")
    print("safe_to_promote:", promoted["safe_to_promote"])

    print("\n== 3. 主记录与来源 ==")
    for master in promoted["masters"]:
        print(
            f"{master['row_id']:<10} 完整度 {master['completeness']} "
            f"合并 {master['merged_count']} 条 来源 {master['source']}"
        )

    print("\n== 4. 被合并的记录仍可按自己的主键查询 ==")
    view = fold.explain("site1-100")
    print("状态:", view["status"], "主记录:", view["master"]["row_id"])
    print("原因:", view["focus"]["decision"])
    print("来源:", view["sources"])

    print("\n== 5. 回放某一条的合并过程 ==")
    for step in fold.replay("site1-100")["steps"]:
        detail = step.get("why") or step.get("field") or step.get("master")
        print(f"  {step['step']}. {step['action']:<13} {detail}")

    print("\n== 6. 人工拆分误合并，并写审计 ==")
    split = fold.split("shop-77", ["site1-100"])
    print("拆出主记录:", split["detached"][0]["master_id"])
    print("审计:", [e["action"] for e in fold.audit("site1-100")])

    print("\n== 7. 再折叠：拆分过的记录保持独立 ==")
    again = fold.fold(ROWS, domain="merchant")
    print("主记录数:", len(again["rows"]), "金额:", again["amounts"])
    print("保持独立:", [item["row_id"] for item in again["kept_separate"]])

    print("\n== 8. 导出（整组明细 / 单条回放） ==")
    export = fold.export()
    print("导出的分组数:", len(export["groups"]), "审计条数:", len(export["audit"]))
    print("单条导出键:", sorted(fold.export("site1-100")))
    path = os.path.join(tempfile.gettempdir(), "rowfold-demo-export.json")
    fold.export(path=path)
    print("已写入", path)


if __name__ == "__main__":
    main()
