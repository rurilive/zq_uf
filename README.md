# attacharchive

附件归档：固定大小分片上传、断点续传、原子合并、清单对账。纯标准库。

## 分片口径
- 按 `chunk_size`（默认 4MiB）切片，每片落盘 `chunks/<key>/NNNNNNNN.part` 并记 sha256。
- 已校验片跳过，只重传缺失或损坏的片；分片经线程池并发写入（`workers`）。
- 合并按序号拼接、逐片校验、核对整体哈希，先写 `tmp/` 再 `os.replace` 原子改名。
- 对象改名后同事务更新 `manifest.json`（tmp+replace）；同 key 重复上传直接返回已完成。

## 对账口径
- `reconcile(manifest, root, repair=False)` 只审计带 sha256 的条目，裸 key 视为声明跳过。
- 输出 `missing`（清单有对象无）、`orphans`（对象有清单无）、`corrupt`（哈希不符）。
- `repair=True` 只补差异：删孤儿和坏对象（坏对象随后按 missing 重新上传），返回 `repaired`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
