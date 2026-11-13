# listwatch

列表页增量监控小工具，纯 Python 标准库。按稳定键识别同一条目，
按内容指纹识别更新，支持跨页去重、真删除/下架区分、本地增量状态和阈值告警。

## 稳定键怎么选

`stable_key(item)` 按以下优先级取键，键相同视为同一条目：

1. 详情链接：`detail_url` / `url` / `link` / `href`，会去掉 `#fragment`、
   `utm_*`、`spm` 等跟踪参数和结尾斜杠，避免链接噪声换键。
2. 业务编号：`id` / `biz_id` / `item_id` / `code` / `no` / `number` / `sn`。
3. 规范化标题（兜底）：NFKC 归一、去全部空白和标点、忽略大小写。
4. 整条内容指纹（连标题都没有时的最后兜底）。

## 内容指纹

`fingerprint(item)` 对条目内容做规范化（文本同样去空白、标点、大小写归一；
链接做同样的 URL 清洗），稳定 JSON 序列化后取 sha256。标题里的空白和标点
差异不算变更；`status`/`state` 以及 `is_online` 等在线标记只用于下架判断，
不进入指纹，因此纯状态变更会归到下架而不是更新。

## 跨页去重

`merge_pages([page1, page2, ...])` 按稳定键去重，分页边界重复的条目只保留
首次出现的一条，并保留首次出现的顺序。

## diff 输出

接口保持 `diff(old, new)`，按稳定键比对，输出四类结果（无内容时该键省略）：

- `added`：新快照里出现的稳定键。
- `updated`：键相同但内容指纹变化（标题空白/标点差异不计）。
- `removed`：旧键在新快照中完全消失，即真删除。
- `delisted`：键还在，但 `status` 变成 `offline`/`下架`/`下线` 等，或
  `is_online` 等在线标记由真变假——只是下架，不是真删除。
- `reordered`：共同键的相对次序发生变化，给出 `key/title/old_index/new_index`；
  纯新增删除引起的位移不算重排。

## 增量状态与告警阈值

`Store` 把每个站点最近一次快照（稳定键 + 条目）原子写入本地 JSON 文件，
支持按站点独立增量运行：

```python
from listwatch import ChangeThresholdExceeded, Store

store = Store("listwatch_state.json")
try:
    result = store.run("my-site", pages, alert_threshold=10)
except ChangeThresholdExceeded as alert:
    result = alert.result  # 状态已先落盘，再决定怎么通知
```

- 首次运行只建基线，不把全量判成新增，`change_count` 为 0。
- 告警变更量 = 新增 + 更新 + 删除 + 下架；顺序变化只是位移，不计入。
- `change_count > alert_threshold` 时抛 `ChangeThresholdExceeded`，状态照常落盘；
  不传阈值则只返回结果不告警。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
