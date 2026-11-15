"""演示：两次提交后回滚，并展示版本链、结构关联、部分表回滚与归档。"""

import json
import shutil
import tempfile

from tableversion import TableVersion


def show(title, obj):
    print("=" * 68)
    print(title)
    print("-" * 68)
    print(json.dumps(obj, ensure_ascii=False, indent=2))


root = tempfile.mkdtemp(prefix="tableversion-demo-")
tv = TableVersion(root=root, committer="alice")
try:
    # -- 第一段：两次提交（第二次带结构变更），再回滚到版本 1 ----------
    r1 = tv.commit({"name": "orders", "rows": [{"id": 1, "amount": 100}]})
    show("提交 1：生成版本、校验和，父版本为空", {
        "version": r1["version"],
        "checksum": r1["checksum"],
        "parent": r1["parent"],
        "committer": r1["committer"],
        "schema": r1["schema"],
    })

    r2 = tv.commit({
        "name": "orders",
        "rows": [{"id": 1, "amount": 100, "status": "paid"}],
    })
    show("提交 2：结构变更随版本一起记录，链上挂到版本 1", {
        "version": r2["version"],
        "parent": r2["parent"],
        "checksum": r2["checksum"],
        "schema_changed": r2["schema_changed"],
        "chain": r2["chain"],
    })

    rb = tv.rollback(1, table="orders")
    show("回滚到版本 1：先校验版本链与校验和，再恢复数据；历史全部保留", {
        "rolled_back": rb["rolled_back"],
        "restored": rb["restored"],
        "recovery_version": rb["tables"][0]["recovery_version"],
        "head_rows": tv.head("orders")["rows"],
        "chain": rb["chain"],
    })

    # -- 第二段：多表部分回滚与过期版本归档 ---------------------------
    tv.commit({"name": "customers", "rows": [{"name": "acme"}]})
    tv.commit({"name": "customers", "rows": [{"name": "acme"}, {"name": "globex"}]})
    tv.commit({"name": "orders", "rows": [{"id": 9}]})
    partial = tv.rollback(1, tables=["orders", "customers"])
    show("部分表回滚：只恢复指定的表，逐表给出结果", partial["tables"])

    for i in range(3):
        tv.commit({"name": "logs", "rows": [{"seq": i}]})
    archived = tv.archive(keep=1, table="logs")
    show("归档：过期版本移入 archive/，不能再作为回滚目标", {
        "archived": archived,
        "archived_versions": tv.list_archived("logs"),
        "live_versions": tv.storage.list_versions("logs"),
    })
finally:
    tv.close()
    shutil.rmtree(root, ignore_errors=True)

print("=" * 68)
print("演示完成。文件后端目录布局：metadata/ 与 data/ 分离，archive/ 存归档。")
