# scrollload

按页面反馈滚动：每次滚动后等 `loading` 消失、`network_idle` 才读取条目，
所以不会把骨架屏当数据；条目按 `id/key/url/href` 去重，虚拟列表重复渲染也安全。

- 停止条件：页面报到底（`at_end` / `has_more is False`），或连续 `idle_rounds`
  次无新增，或到达 `max_rounds`，或等待超时 / 页面报错。不看高度，虚拟列表高度
  不变也能判到底。
- 失败保留现场：`errors[*]` 含异常、traceback、页面快照和已见条目尾部；
  恢复时传 `seen=上次结果` 即可从已见条目继续，不重复计数。
- 返回 `rounds`、`added`、`reason`、`seen`、`seen_items`、`items`、`skeletons`、
  `site`、`errors`、`events`；`StopStats` 按站点累计停止原因。
- 短列表一次到底不滚；长列表滚到页面报到底为止。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

`done(page)` 只依据 `at_end` / `has_more` / 加载状态判断，不使用高度。
