# dirprobe

安全方向的目录探测小工具，按字典逐条探测站点路径，节奏跟随目标反馈自适应。

## 能力

- **自适应并发**：响应变慢（`ms` 超阈值）或出现拦截页（429 / 响应体指纹）时
  逐档下调并发，连续干净批次后再逐档提回上限，下限为 1。
- **拦截不算命中**：429 与拦截指纹页只计入拦截，不进命中列表；被拦路径留待
  后续轮次重试，重试预算用尽后标记为“确认拦截”，不再重复请求。
- **断点续跑**：每批结束把进度落盘到 `state_dir`（默认 `.dirprobe_state/`），
  中断后重新 `load` 同一字典即从续跑位置继续，已确认路径不重复请求。
- **整站拒绝保护**：多个从未请求过的新路径都被拦时，判定站点整体拒绝访问，
  停止本轮并保留已完成进度，拒绝页不算空结果。
- **命中归类与参数合并**：命中按目录 / 文件 / 备份 / 管理入口归类；
  同一路径的不同查询参数合并成一条，参数名汇总保留。

## 用法

```python
from dirprobe import load, probe, report

load("shop", words, responses)          # responses: {path: {"status", "body", "ms"}}
out = probe("shop", concurrency=8)       # 返回 hits/blocked/concurrency/resume 等
rep = report("shop")                     # 按站点查看进度、分类与被拦截批次
```

`probe(site)` 与 `report(site)` 调用方式与旧版一致，返回值在原有字段上
补充 `blocked_batches`、`blocked_paths`、`settled_blocked`、`progress` 等信息。
运行 `python demo.py` 看完整演示，`python -m unittest discover -s tests` 跑测试。
