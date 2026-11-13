# pagesnap

页面快照：去重存储、按站点/时间索引、结构比对、按保留天数清理。纯标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 目录结构
- `objects/<sha256>.txt[.gz]`：正文，按内容哈希去重，相同内容只存一份；`compress=True` 时 gzip 压缩
- `objects/<sha256>.meta.json`：关键结构（HTML 标签序列）与哈希
- `index/<site>/<page_id>/<时间戳>.json`：索引条目，指向内容哈希

## 接口
- `save(page, directory, retention_days=None, compress=False)`：page 含 `id/text`，可选 `site/time/html/structure`；返回正文路径，传入保留天数时顺带清理过期快照
- `get(page_id, directory, site="default", at=None)`：取不晚于 `at` 的最新快照
- `diff(a, b)`：比对该两个快照文件；`text_change_percent`/`structure_change_percent` 基于 difflib 相似度（0–100），`added_nodes`/`removed_nodes` 为结构增删的标签，`change_percent` 为两者均值
