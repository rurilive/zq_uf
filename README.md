# snapalign

快照与变更流按位点对齐，恢复零丢失。

- 变更带单调位点（`ChangeStream`），`snapshot` 记录起始位点，快照期间变更进缓存
- `resume` 按位点顺序应用：upsert 按主键覆盖、delete 缺行无操作，重放幂等
- 位点只向前；坏事件抛 `AlignError` 停在原位点，`repair` 修复后 `resume` 续传
- `verify` 按表比对行数与内容，结果含位点、缓存数、应用数与逐表差异

```bash
python3 demo.py                      # 测试库恢复演练
python3 -m unittest discover -s tests
```
