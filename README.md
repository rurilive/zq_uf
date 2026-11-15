# auditquery

现状：审计全量进内存，精确匹配，偏移分页。

问题：几百万条内存放不下，翻页重复或漏，结果无序。

目标：流式过滤，游标带指纹，翻页不重不漏并按时间排序。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

实现要点：
- `search(query, size)` 流式过滤：精确字段 + `start`/`end` 时间范围，结果按 (ts, 序号) 倒序，只物化一页。
- 返回 `total`（命中数）、`cursor`（下一页位置）、`truncated`（超上限截断）、`stale`（数据变化提示）。
- 游标带查询条件、位置标记和数据指纹；键集定位，新数据插入不重不漏。
- `page(cursor)` 翻页；指纹不符时 `stale=True` 提示一次。
- 索引懒构建，不可用（`configure(use_index=False)` 或构建失败）时退回单遍扫描 + 堆取页，结果一致。
- 上限可配：`configure(page_size=, max_page_size=, max_hits=)`。
- 验证：`tests/test_search.py` 在 5000 条历史数据上翻页断言不重不漏，插入、回退、截断均有用例。
