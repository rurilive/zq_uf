# seedscore

入队前给种子打分，再按分数决定抓取名次。

## 解决什么

- 垃圾页/重复页先占配额：入队前评分，低于阈值不进队。
- 同目标从不同来源重复进来：URL 归一化后按目标去重，只留分最高的一条。
- 失效种子反复重试：失效种子单独标记，并从队列移除。
- 评分误杀新站点：人工可恢复；新评分先影子运行，与人工挑选对比后才启用。

## 快速开始

```bash
python3 demo.py                                   # 六段演示
python3 -m unittest discover -s tests             # 105 个用例
python3 -m seedscore filter seeds.json --export report.csv
```

## API

```python
from seedscore import score, filter, shadow, ReviewLog, diff_reports

score(seed)                       # 分数构成 + 阈值判断 + 失效原因
filter(seeds, queue=当前队列)      # kept / dropped / dead / reasons / by_site
shadow(seeds, human=人工挑选)      # 影子对比，verdict: safe/blocked/insufficient_coverage
log = ReviewLog("restores.jsonl") # 人工恢复，可 export/query/replay
```

`seed` 为字典：`url`、`source`/`source_type`、`history`（`successes`/`failure`
/`last_success` 等）、`status`、`http_status`、`expires_at`、`is_new`。

## 评分

三个可解释分量加权求和（权重归一化，默认 0.35 / 0.35 / 0.30）：

| 分量 | 依据 |
| --- | --- |
| `source_credibility` | 种子自带值 > 按来源名配置 > 来源类型表 > 站点质量 > 中性 0.5 |
| `history_yield` | 平滑成功率 + 产出量置信度 + 最近成功新鲜度；无历史为中性，不误杀 |
| `link_quality` | HTTPS、路径深度、垃圾路径/参数、二进制/feed 扩展名、可疑 TLD、IP 直连 |

返回里 `parts` 是原始分量，`contributions` 是加权后贡献，`notes` 是逐条依据。

按站点调整评分：`config = {"sites": {"news.example.com": {"threshold": 0.4,
"parts": {"link_quality": 0.8}}, "*.blog.example": {"bonus": 0.05},
"junk.example": {"blocked": True}}}`。精确 host 优先，其次最长后缀，最后 `"*"`。
站点规则里也可写 `weights`、`adjust`、`penalty`、`known`、`sources`。

## 队列语义

- **阈值**：`score < threshold` 剔除；`blocked` 站点强制剔除。
- **去重**：同 `target`（忽略 scheme、`www.`、追踪参数、锚点、参数顺序）只留最高分。
- **稳定顺序**：已在 `queue` 里的目标保持原相对顺序；重算不会打乱已入队顺序，也不会因
  分数跌破阈值被踢出（会标注 `existing_queue:below_threshold_retained`）。
- **失效**：`status`/`http_status`/`expires_at`/错误信息命中失效规则时进 `dead`，
  若在队列中则同时出现在 `queue_removed`。
- **配额**：`limit` 只约束新入队项，不驱逐已入队项，也不驱逐人工恢复项。
- **恢复**：`restored`/`ReviewLog` 中的目标无视阈值重新入队，排在已有队列之后。

## 影子运行

`shadow()` 只评分不入队，把评分结果与人工挑选对比：

- `blocked`：有人工挑选的种子会被误杀。
- `insufficient_coverage`：未发现误杀，但有被剔除的**新站点**不在复核样本内，证据不足。
- `safe`：无人工挑选被误杀，且所有有风险的新站点都已被复核。

采样按 `seed` 做稳定哈希，同样入参可完全复现。

## 恢复记录与导出

恢复是追加式 JSONL，事件含内容哈希 `id`、目标、操作人、原因、时间，可撤销（追加
`revoke`）。支持按操作人/目标/站点/时间查询，导出 JSONL/CSV，按时间点回放。

```bash
python3 -m seedscore restore https://site/page --log restores.jsonl \
    --actor alice --reason "新站点误杀"
python3 -m seedscore query --log restores.jsonl --site site --export q.csv --export-format csv
python3 -m seedscore replay --log restores.jsonl --until 2026-11-01T00:00:00Z
```

`diff_reports(old, new)` / `filter(..., previous=old)` 给出新增、移除、分数变化与位置变化，
并按站点聚合，便于比较两次评分版本。
