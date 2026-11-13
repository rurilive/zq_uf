# paginate

`walk(fetch_page)` 抓完全部页，按页序返回去重后的条目。纯标准库。

- 响应约定：返回 list 即一页（页码从 1 递增）；返回 dict 时取 `items`/`data`/`results`，含 `next_cursor`/`cursor` 走游标模式，否则按 `page`/`next_page`/`first_page`/`total_pages` 走页码模式，模式切换以响应字段为准。
- 起始页码以首个响应的 `first_page`/`page` 为准（0 基接口不漏首屏）；总页数以最后一次响应为准，允许中途变化。
- 结束条件：空页；整页命中滑动窗口（`dedup_window`）的重复页；游标为空；已抓页数达到最新 `total_pages`。
- 重试：`OSError` 族（连接/超时）按指数退避重试 `max_retries` 次，同页原地重试不乱序；其他异常保存进度后抛出。
- 恢复：传 `state_path` 即每页落盘（下一 token、已抓条目、去重窗口），中断后重跑自动从断点续抓，正常完成后删除状态文件。
- 并发：`concurrency>1` 时按页批量并发抓取，输出始终按页序，与完成顺序无关。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
