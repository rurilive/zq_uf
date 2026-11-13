"""造热、温、冷三组访问数据，演示干跑和真实迁移。输出确定性，便于断言核对。"""
import os
import shutil
import tempfile

from tiering import apply, plan, record_access

NOW = 1_700_000_000.0
DAY = 86400
SIZE = 100

GROUPS = {
    "hot": ("hot_a.parquet", "hot_b.parquet"),
    "warm": ("warm_a.parquet", "warm_b.parquet"),
    "cold": ("cold_a.parquet", "cold_b.parquet"),
}
ACCESS = {
    "hot": {"hits": 20, "age_days": 1},
    "warm": {"hits": 5, "age_days": 30},
    "cold": {"hits": 1, "age_days": 200},
}


def build_dataset(root):
    for tier, names in GROUPS.items():
        for name in names:
            path = os.path.join(root, name)
            with open(path, "w") as fh:
                fh.write("x" * SIZE)
            rule = ACCESS[tier]
            record_access(root, name, hits=rule["hits"],
                          at=NOW - rule["age_days"] * DAY)


def main():
    root = tempfile.mkdtemp()
    try:
        build_dataset(root)
        payload = plan(root, dry_run=True, now=NOW)
        print("分层:", {m["name"]: m["tier"] for m in payload["moves"]})
        print("干跑:", apply(payload))
        print("实迁:", apply(plan(root, now=NOW)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
