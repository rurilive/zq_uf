# attachcheck

附件下载：断点续传、内容寻址、长度与内容校验、隔离区重试。

## 行为

- **先写中间文件**：内容写进 `.attachcheck/parts/`，目标目录不会出现半个文件。
- **断点续传**：每 1 MiB 把断点和已下载前缀的 sha256 落盘；下次从断点续传，
  续传前先校验已下载部分，对不上就从头重下。请求带 `Range` 与 `If-Range`，
  站点换过内容时自动放弃旧断点。
- **完成后才改名**：校验通过后用硬链接（不支持时用独占创建复制）落到正式
  文件名，既有文件永不被覆盖。
- **同名按内容区分**：同名但内容不同时，第二个文件落到 `name.<哈希前缀>.ext`，
  两个版本都保留。
- **重复下载复用**：同一地址再下载时，文件仍与记录一致就直接复用，不再请求站点。
- **按长度和内容校验**：长度取自 `Content-Range`/`Content-Length`，内容用
  sha256；站点若声明 `Digest`/`Content-MD5` 也会复核。坏文件进隔离区。
- **隔离区**：失败记录保留原始 URL、状态码、已下字节和断点，`retry()` 可重试；
  重试成功自动清除记录。
- **单站点观察**：`canary_sites=["shop.example"]` 时只有该站点启用续传与隔离，
  其他站点仍走原来的单次下载，正常附件不受影响。

`fetch` 返回 `path, bytes, received, total, resumed, reused, ok, status, error,
quarantine, verified`。

## 用法

```python
from attachcheck import AttachmentStore

store = AttachmentStore("/data/attachments")
print(store.fetch("https://shop.example/a.pdf", "/data/attachments/a.pdf"))
print(store.verify("/data/attachments/a.pdf"))
for entry in store.list_quarantine():
    print(store.retry(entry))
```

## 试跑

```bash
python3 demo.py                        # 离线：断线续传 + 隔离区重试
python3 -m unittest discover -s tests  # 20 个用例，全部离线
```

测试用 `attachcheck.ScriptedTransport` 模拟站点（断线、忽略 Range、内容更新、
错误码），不需要网络。
