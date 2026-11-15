"""长尾数据上的增量画像演示。"""
import os
import random
import tempfile

from dataprof import configure, export_profile, history, observe, profile

random.seed(2026)


def long_tail(n):
    """98% 小延迟 + 2% 极端大值（长尾）。"""
    values = []
    for _ in range(n):
        if random.random() < 0.98:
            values.append(round(random.expovariate(1 / 20.0), 2))
        else:
            values.append(round(random.uniform(500.0, 5000.0), 2))
    return values


def main():
    storage = os.path.join(tempfile.gettempdir(), "dataprof_demo")
    configure(storage_dir=storage, keep_versions=3,
              field_thresholds={"latency": {"multiplier": 3.0}})

    # 第一段：分 4 批喂入长尾数据，画像按数据版本增量更新
    for _ in range(4):
        rows = [{"latency": v, "region": random.choice(["cn", "us", None])}
                for v in long_tail(500)]
        result = observe(rows)
        print("batch -> version", result["version"], "total", result["total"])

    p = profile("latency")
    print("mean      ", round(p["mean"], 2))
    print("quantiles ", {k: round(v, 2) for k, v in p["quantiles"].items()})
    print("threshold ", p["outlier_threshold"])
    print("outliers  ", p["outlier_count"], "samples:", p["outliers"][:5])
    print("null_rate ", round(profile("region")["null_rate"], 3))

    # 第二段：落盘、导出与历史版本
    out = os.path.join(storage, "export.json")
    export_profile(path=out)
    print("persisted ", sorted(os.listdir(storage)))
    print("history   ", [snap["version"] for snap in history()])
    print("exported  ", out)


if __name__ == "__main__":
    main()
