# sampler

可复现的分层采样与统计摘要，纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口
- `sample(rows, plan)` 返回 `SampleResult`（list 子类），`summary(sample)` 返回 `Summary`（dict 子类）。
- `Summary` 的键固定为 `count`/`mean`（兼容旧断言），分位数在 `.quantiles`，误差在 `.error`，`.to_dict()` 可 JSON 序列化。

## plan 参数
- `strata`：分层字段名（如 `"city"`），按字段值分组逐层抽样；行缺该字段抛 `ValueError`，非 dict 行抛 `TypeError`。
- `n`：固定总条数（正整数），按层规模用最大余数法分配；`n >= 层数` 时非空层至少 1 条（保护长尾）；`n >= 总行数` 返回全表。
- `ratio`：比例抽样，取值 `(0, 1]`，每层取 `max(1, round(层大小 * ratio))`；与 `n` 互斥，同给或越界抛 `ValueError`。
- `random_seed`：固定种子，给定则两次结果完全一致；不给则沿用全局 `random`（兼容旧行为）。旧键 `seed` 保留但忽略。
- 都不给时默认 `n=10`。

## 误差口径
- `mean` 为样本均值；`quantiles` 为 p50/p90/p99，线性插值（位置 `p*(n-1)`）。
- `error` 为均值 95% 置信区间半宽：`1.96 * s / sqrt(n)`，`s` 为样本标准差（ddof=1）；`n < 2` 时为 `None`。
- 按比例分层时样本自加权，均值与分位数可直接估计总体。

## 重放
- `result.to_state()` 返回可 JSON 序列化状态（plan、seed、各层条数）。
- 重启后 `replay(rows, state)` 或 `sample(rows, state["plan"])` 在相同输入下复现同样样本。
