# apipage

游标分页加断点恢复，纯标准库。`fetch(token)` 首页固定 `fetch(1)`，返回
`{"items": [...], "next_cursor": "..."}`，`next_cursor` 为 None 表示到底；
旧 `next_page` 页码格式继续兼容。游标失效时 fetch 抛 `CursorExpired`，
walk 自动回首页重取并按已抓键跳过整页；同一游标连续出现两次判重复并停止。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## walk(fetch, state) 输出

`items` 本次新条目；`page` 下一页页码；`pages` 出新条目的页数；
`skipped_pages` 整页已抓而跳过的页数；`expired` 游标恢复次数；
`duplicate` / `duplicate_cursor` 重复停止标记；`resumed` 是否断点启动；
`state` 可落盘的版本化断点。`resume(state)` 校验并迁移断点。

## 断点状态字段（version=2）

`version` 格式版本；`cursor` 下一次请求的游标（None 时用 page）；
`page` 下一页页码；`seen_keys` 已抓条目键（dict 取 id）；
`pages_fetched` 累计已抓页数。`save_checkpoint/load_checkpoint` 负责文件读写，
无 version 的旧格式 `{"page": n}` 读取时自动迁移，不报错。
