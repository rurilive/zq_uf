# qtrend2

固定阈值质量告警的基线化升级。

- **基线**：最近窗口 + 同周期（季节性）样本，中位数 / MAD 稳健统计，业务量平移不跟着误报
- **两类告警**：突变（稳健 z 分）即时告警；缓慢漂移连续 N 个周期同向偏离才确认
- **告警带上下文**：基线、偏离幅度、z 分、基线样本、最近历史片段
- **误报抑制**：值班标记误报后不重复提醒，偏离消失自动重新武装
- **趋势**：按 dataset × metric 独立统计方向与 OLS 斜率
- **回测**：同一引擎、同一份数据对比旧固定阈值，支持阈值网格扫描
- **影子运行**：只记录不告警，按人工标签输出新旧规则误报率对比

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

```python
from qtrend2 import Engine, Config

eng = Engine(Config(window=14, season=7, direction="down"))
r = eng.check("orders", "error_rate", 0.01)   # 摄入并评估
eng.mark_false_positive("orders", "error_rate")  # 标记误报，后续抑制
eng.trend("orders", "error_rate")             # 趋势方向与斜率
eng.backtest(points)                          # 回测：新基线 vs 固定阈值
eng.sweep_thresholds(points)                  # 阈值网格回测
rec = eng.check("orders", "error_rate", v, shadow=True)  # 影子运行
eng.label_shadow(rec["alert_id"], 0)          # 人工标签：0 误报 / 1 真实
eng.shadow_report()                           # 误报率对比报告
```
