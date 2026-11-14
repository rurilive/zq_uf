"""模拟一次隔离恢复演练：造一份快照，恢复到临时目录并校验，输出报告。"""

import hashlib
import json
import os
import tempfile

from restoredrill import configure, drill, report


def build_snapshot(root, name="snap-demo"):
    snap = os.path.join(root, name)
    chunks = os.path.join(snap, "chunks")
    os.makedirs(chunks)
    manifest = {"snapshot": name, "created_at": "2026-11-14T08:00:00+00:00",
                "key_tables": ["users"], "tables": {}}
    for table, rows in {"users": ["u1,alice", "u2,bob", "u3,carol"],
                        "orders": ["o1,u1", "o2,u2"]}.items():
        data = "".join(r + "\n" for r in rows).encode("utf-8")
        with open(os.path.join(chunks, "%s-0001.csv" % table), "wb") as fh:
            fh.write(data)
        manifest["tables"][table] = {
            "chunks": ["%s-0001.csv" % table], "rows": len(rows),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    with open(os.path.join(snap, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)


def main():
    work = tempfile.mkdtemp(prefix="restoredrill-demo-")
    configure(snapshot_root=work,
              archive_path=os.path.join(work, "archive.jsonl"))
    build_snapshot(work)

    result = drill("snap-demo")
    print("演练结论:", result["conclusion"])
    print("RTO(恢复耗时): %.3fs  RPO(快照年龄): %.1fs"
          % (result["rto_seconds"], result["rpo_seconds"]))
    print("各表校验:", json.dumps(result["checks"], ensure_ascii=False))

    summary = report(snapshot="snap-demo")
    print("报告: drills=%d passed=%d failed=%d"
          % (summary["drills"], summary["passed"], summary["failed"]))
    print("按表查询 users:",
          json.dumps(report(table="users")["tables"]["users"][0]["status"],
                     ensure_ascii=False))
    print("留档文件:", os.path.join(work, "archive.jsonl"))


if __name__ == "__main__":
    main()
