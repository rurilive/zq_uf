# seedexpand

种子链接扩散模块。按深度和每层配额扩散，跨批去重，记录来源。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `expand(page, depth)`：展开深度为 `depth` 的页面。`page` 形如
  `{"url": ..., "site": ..., "links": [...]}`；`site` 缺省取 URL 主机名
  （另一个站点则各自独立配额）。返回：
  - `depth`：回显传入深度（兼容旧行为）。
  - `added` / `added_detail`：新增目标 URL / 附带分类、得分、深度、来源页的记录。
  - `truncated`：被截断目标及原因（`depth-quota`、`category-cap`、`max-depth`、`media`）。
  - `duplicates`：批内或跨批重复（`duplicate-in-batch`、`already-enqueued`），附首次来源。
  - `depth_counts`：各层已入队数量；`queue_depth`：站点已入队总数；`enqueue_rate`：本次入队速率。
  - `layer_budget`：本次目标层的 `limit` / `used` / `remaining`。
- `budget(site)`：站点预算快照。`limit` 为站点总上限，默认 `None`（不设总上限），
  逐层上限在 `layers[d]["limit"/"used"/"remaining"]`；另含 `depth_counts`、
  `queue_depth`、`enqueue_rate`、`targets`（每条含来源页与深度）。
- `reset(site=None)` / `configure(site, quotas=, max_depth=, category_caps=)`：
  清空或调整站点状态，便于测试与调参。

## 扩散口径

- 深度：页面深度 `d` 上的链接入队时深度为 `d + 1`；`d + 1 > max_depth` 时一律截断
  （`max-depth`），默认 `max_depth=3`。
- 每层上限：按（站点, 目标深度）计数，**跨批累计**，同一批内超出即截断；
  默认 `{1: 40, 2: 20, 3: 8}`，越深越严。截断时按优先级得分取高价值目标。
- 跨批去重：所有已入队目标记入站点 seen 集合，之后任何批次再出现即记为
  `already-enqueued`，不再入队；批内重复记为 `duplicate-in-batch`。
- 规范化：`http`/`https` 视为同一目标，忽略主机大小写、默认端口、重复斜杠、
  尾斜杠和 `#fragment`；查询串保持原样参与去重。

## 优先级与分类

依据链接特征打分，分数越高越先保留：

| 分类 | 特征 | 基础分 |
| --- | --- | --- |
| content | 路径含 `item`/`post`/`article`/`product` 等，或 `?id=`、`?item_id=` 等参数 | 100（带内容参数 +5） |
| list | 路径含 `category`/`page`/`archive` 等，或 `?page=`/`?offset=` 等分页参数 | 60（带分页参数 +5） |
| other | 其余普通链接 | 40（纯数字路径 +10） |
| nav | `about`/`login`/`privacy`/`home` 等导航链接 | 20，每层单独限流，默认 `nav` 上限 2 |
| media | 图片、脚本、音视频、压缩包等后缀 | 0，默认不扩散 |

导航与列表页单独处理：导航每层单独限额，列表页参与内容优先级但得分低于内容页。
截断时优先保留高价值目标。

## 日志

`seedexpand` logger 每次扩散输出一行：
`site`、`source`、`depth`、目标层、新增/截断/重复数、`queue_depth`、`enqueue_rate`。
调用方配置 `logging.basicConfig(level=logging.INFO)` 即可看到队列深度与入队速率。
