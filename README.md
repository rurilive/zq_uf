# archivepack

采集附件归档：内容哈希命名、流式读写、中断续传、引用计数去重、清单校验恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 目录结构

```
root/
  objects/<哈希前2位>/<sha256><扩展名>   # 去重后的附件内容，相同内容只存一份
  manifest.json                         # 清单，恢复前按它逐条校验
  refs.json                             # sha256 -> 引用计数
  .tmp/<url哈希>.part                   # 续传临时文件，写完原子改名后消失
```

## 清单格式

`manifest.json`：`{"version": 1, "files": [{"url", "name", "size", "sha256", "path"}]}`，
每条记录原始 URL、安全化文件名、大小、校验和与归档相对路径。

- `pack(files, root)`：`data` 支持 bytes 或文件流，64KB 分块写入 `.part`，中断后重跑自动续传；
  返回 `{"added", "reused", "failed", "paths", "entries"}`。
- `restore(root)`：按清单校验存在性、大小和 sha256，每条记录带 `status`（ok/missing/corrupt）。
