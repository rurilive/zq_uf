"""sampleci 端到端演示。

运行：python3 demo.py
1. 固定种子的分层采样，连跑两次逐行一致；比例与固定条数按层混用；
2. 先在一个指标上与全量结果对比，误差区间可接受才视为计划通过；
3. 长尾层单独统计，结果导出 JSON / CSV。
"""
import random

from sampleci import (
    compare,
    full_scan,
    replay,
    sample,
    summarize,
    write_files,
)


def build_rows(n=3000, seed=7):
    """A/B 两个主体层 + C 稀有长尾层（1% 行，贡献极端大值）。"""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        if i % 100 == 0:
            group, value = "C", 10_000 + rng.random() * 5_000
        elif i % 2 == 0:
            group, value = "A", rng.gauss(100, 10)
        else:
            group, value = "B", rng.gauss(50, 5)
        rows.append({"id": i, "group": group, "value": value})
    return rows


def main():
    rows = build_rows()
    plan = {
        "seed": 20261116,
        "strata_field": "group",
        "strata": {"A": {"ratio": 0.2}, "B": {"n": 200}},  # 比例 + 固定条数混用
        "ratio": 0.1,            # C 层兜底比例（但它是长尾，会自动普查）
        "tail_min_count": 30,
        "quantiles": [0.5, 0.9, 0.95, 0.99],
    }

    print("=" * 64)
    print("1) 可复现：同一计划两次采样 + 重放")
    print("=" * 64)
    first = sample(rows, plan)
    second = sample(rows, plan)
    replayed = replay(rows, plan, first)
    print(f"总量 {first['n_total']}，抽样 {first['n_sampled']}，"
          f"种子 {first['seed']}")
    print(f"两次采样索引一致：{first['indices'] == second['indices']}")
    print(f"按录制计划重放一致：{replayed['indices'] == first['indices']}")
    for label, meta in first["strata"].items():
        flag = "（长尾→普查）" if meta["tail"] else ""
        print(f"  层 {label}: {meta['sampled']}/{meta['size']}，"
              f"模式 {meta['mode']}{flag}")

    print()
    print("=" * 64)
    print("2) 单指标先对全量：均值区间覆盖真值才推广（分位仅提示）")
    print("=" * 64)
    stats = summarize(first)
    truth = full_scan(rows)
    verdict = compare(first, truth, max_relative_bias=0.05)
    mean_check = next(c for c in verdict["checks"] if c["metric"] == "mean")
    print(f"全量均值={truth['mean']:.4f}  采样均值={stats['mean']:.4f}")
    print(f"95% 置信区间=[{stats['ci'][0]:.4f}, {stats['ci'][1]:.4f}]，"
          f"覆盖真值：{mean_check['ci_covers_true']}，"
          f"相对偏差={mean_check['relative_bias']:.4%}")
    print(f"验收结论：{verdict['verdict']}（均值为硬门槛，分位不阻断）")
    print("分位数（与均值分开说明；加权=总体口径，等权=样本口径）：")
    for q, item in stats["quantiles"].items():
        ci = item["ci"]
        band = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "无区间（仅点估计）"
        print(f"  P{float(q) * 100:g} 加权={item['estimate_weighted']:.2f} "
              f"等权={item['estimate_sample']:.2f}  "
              f"bootstrap95%={band}  全量={truth['quantiles'][q]:.2f}")

    print()
    print("=" * 64)
    print("3) 长尾层单独统计 + 结果导出")
    print("=" * 64)
    tail = stats["tail"]["summary"]
    print(f"长尾层 {stats['tail']['labels']}："
          f"{tail['n_sampled']} 条全部普查，均值={tail['mean']:.2f}，"
          f"独立区间={tail['ci']}")
    paths = write_files("/tmp/sampleci_demo", first, stats, verdict)
    for path in paths:
        print(f"已导出：{path}")


if __name__ == "__main__":
    main()
