# linkguard

`follow(url, fetch)` 跟随跳转链；无跳转时原样返回响应，否则返回报告。

- 判环：URL 规范化（小写 host、去默认端口、去会话参数、排序查询）后入已访问集合，重复即报错返回。
- 上限：默认最多跟 10 跳，`max_redirects` 可调，超限返回错误报告。
- 跨域：记录来源和目标到 `cross_domain`，默认不跟随，`follow_cross_domain=True` 可放行。
- 报告：`final_url`、`redirects`、`cycle`、`error`、`chain`（跳转链摘要，便于复盘）、`response`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
