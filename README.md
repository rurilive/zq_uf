# rowrepair

行级修复与回放：坏行隔离保留原文，好行入库，修复后按批幂等回放并对账。纯标准库。

## 隔离口径
- 坏行（非映射 `not_a_mapping` / 缺 id `missing_id` / ok 为 False `flagged_bad`）进隔离区，记录原始行、原因、批次号、批内序号。
- 坏行比例达到 0.5 整批暂停（`rolled_back`）：好行暂存不入库，现场保留，修复后回放放行。

## 回放口径
- `replay(batch)` 按批回放，传批次列表可部分回放；按 id 幂等，已入库不重复，重复回放不产生重复行。
- 回放前重新校验隔离区中经 `fix(qid, row)` 修复的行，通过即入库，仍坏则留在隔离区并更新原因。
- 结果带对账：expected / stored / quarantined / missing，未入库按原因归类（`by_reason`），`matched` 表示账实相符。
- `progress()` 查回放进度，`export_quarantine()` 导出隔离数据（可写 JSON 文件）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
