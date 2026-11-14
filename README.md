# tablesync

纯标准库的表增量同步模块：按主键 + 水位只同步变化行，删除走墓碑传播，
每批写前写后校验行数和校验和，批次确认后推进水位，可回滚到任意已确认水位并重放。

## 接口

- sync(table, rows, watermark=None, expected=None)：同步一个批次，返回批次号、水位、写入/删除/跳过数和校验结果。
- rollback(watermark, table=None)：回滚到指定水位的检查点；多表水位重名时传 table 消歧。
- progress(table=None)：按表查询同步进度（水位、批次数、行数、墓碑数、失败状态、最近批次结果）。
- snapshot(table=None)：查看目标表当前数据；reset(table=None) 清空状态。

## 水位口径

- 每行可带 _wm 表示该行的变更水位；不带则取批次水位 watermark（不传则当前水位自增）。
- 行水位不高于该主键已应用版本时视为过期，跳过并计入 skipped，重复同步因此幂等。
- 批次复核通过后，表水位才推进到 max(当前水位, 批次水位)，并记录检查点。
  校验失败的批次不推进水位、不产生检查点，停在该批，后续可原样重试。

## 墓碑删除

未出现在批次里的行保持不动；删除必须显式传墓碑行（含 id 且 _deleted 为真），
目标行被删除并记录墓碑。更高水位的普通行可在墓碑后重新插入（复活）。

## 批次校验

写前根据批次操作推算变更数、变更校验和（SHA-256）以及目标表行数/校验和；
expected 可传入调用方独立推算的四项（changes/checksum/rows/target_checksum）做写前校验。
写后复核目标表实际行数与校验和，任一不一致则整批回滚、水位不动、返回 verified=False 及 reason。

## 回滚与重放

rollback(watermark) 把目标表、行版本和墓碑还原到该水位的检查点，并丢弃之后的批次记录；
之后按原水位重放批次即可得到相同结果。水位 0 为建表空表检查点；rollback(None) 不支持。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

