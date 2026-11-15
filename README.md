# rowfold

现状：主键相同只留第一条，完整行可能被丢弃。

问题：同实体不同主键重复，取舍没有来源。

目标：主键加相似度折叠，按完整度选主行，保留取舍原因。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现（`rowfold.fold`）：同主键按完整度→更新时间→到达顺序选主记录；异主键在关键字段相似度（一致字段数 / min(双方已填字段数)）≥ 阈值且一致字段数 ≥ `min_shared` 时合并。被合并记录保留来源、相似度与取舍原因，可按自己的主键 `explain()`；主记录变更与人工 `split()` 写审计，`replay()` 回放；`dry_run()` 单业务域试运行、核对金额后 `promote()`；拆分过的记录默认不再自动合并（`unsplit()` 恢复）；`export()` 导出明细。
