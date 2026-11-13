# pagecycle

现状：顺着next一直走，只有页数上限兜底，没有跨页已访问集合。

问题：参数组合出环后反复抓同一页，排序参数被当成新页，断点恢复又重复。

目标：按路径和关键参数归一判环，排序参数忽略，断点沿用集合并给停止原因。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 归一键

- 判环键 = scheme/host 小写 + 路径 + 关键查询参数（按名排序），去掉 fragment。
- 排序参数（sort/order/dir 等）、展示参数（view/layout/theme 等）、
  会话与追踪参数（sid/session/token/utm_* 等）不参与判环。
- page、cursor、offset 等业务参数都是关键参数，页码和游标混合翻页同样判环。
- 用法：walk(fetch, start=..., stop_on_cycle=True)；
  传 state_path 后每页把 pages/visited/next 落盘，resume=True 断点续抓。

## 停止条件

- done：next 为空，正常翻完。
- limit：达到页数上限（兜底，未开判环时环路由它截断）。
- cycle：归一键命中跨页已访问集合，立即停止并记录 repeated。
- repeat：与上一页同键的连续重复（如自环、仅排序参数不同），立即停止。

