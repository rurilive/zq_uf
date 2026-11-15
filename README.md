# crawlindex

采集索引模块（纯标准库）。接口：`rebuild()` 全量重建、`query(key)` 精确查询；另有 `update()` 增量、`progress()` 进度、`version()` 版本。

重建口径：旁路构建新索引 → 校验（条目数 + 抽样内容比对）→ 落盘 → 原子切换；构建期间旧索引继续服务，查询不加锁、延迟不上升。
切换口径：切换前把重建期间的增量更新按版本号合并（版本单调递增，高版本覆盖低版本）；校验失败或切换失败自动回退旧索引，回退写入 `rollback.log`。
续跑与落盘：重建可中断，断点存 `rebuild_checkpoint.json`，下次 `rebuild()` 自动续跑；索引与元数据（版本、条目数）落盘在数据目录（默认 `.crawlindex/`，可用环境变量 `CRAWLINDEX_DIR` 或 `configure(data_dir=...)` 修改）。
兼容：`rebuild()` 返回中的 `checked` 为旧接口字段、恒为 `False`；校验结果看 `validated` 与 `validation`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
