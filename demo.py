"""长尾数据上的可复现分层采样演示。"""
import json

from sampler import replay, sample, summary

PLAN = {"strata": "city", "ratio": 0.05, "random_seed": 42}


def build_rows():
    """长尾数据：bj/sh 小额订单为主，sz 只有 10 条超大单。"""
    rows = []
    for index in range(1000):
        rows.append({
            "id": index,
            "city": "sh" if index % 10 == 0 else "bj",
            "value": index % 50,
        })
    for index in range(10):
        rows.append({"id": 1000 + index, "city": "sz", "value": 100000 + index})
    return rows


def main():
    rows = build_rows()
    first = sample(rows, PLAN)
    second = sample(rows, PLAN)
    assert first == second, "固定种子下两次采样必须一致"

    state = first.to_state()
    replayed = replay(rows, json.loads(json.dumps(state)))
    assert replayed == first, "序列化后重放必须一致"

    part = summary(first)
    full = summary(rows)
    print("分层字段: city, 比例: 0.05, 种子: 42")
    print("各层样本数:", first.strata_counts)
    print("可复现:", first == second, "| 重放一致:", replayed == first)
    print("样本: count=%d mean=%.2f p50=%.1f p90=%.1f p99=%.1f error=%.2f"
          % (part["count"], part["mean"], part.quantiles["p50"],
             part.quantiles["p90"], part.quantiles["p99"], part.error))
    print("全量: count=%d mean=%.2f p50=%.1f p90=%.1f p99=%.1f"
          % (full["count"], full["mean"], full.quantiles["p50"],
             full.quantiles["p90"], full.quantiles["p99"]))


if __name__ == "__main__":
    main()
