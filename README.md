# manifestcheck

现状：清单直接覆盖写，校验只看文件在不在。

问题：缺文件发现不了，大小和校验和不比对，写坏整批失败。

目标：清单记录大小和校验和，原子写，校验缺失、多余和损坏。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

```python
from manifestcheck import write, verify, repair, repair_log, export_diff, export_manifest
write({"path": "manifest.json", "data_dir": "data",
       "files": [{"name": "part-0", "shard": 0}]})   # 原子写，版本只增，失败不覆盖
verify("manifest.json", data_dir="data", shard=0)    # 先在一个分片试运行；列出缺失/多余/损坏
repair("manifest.json", source_dir="backup", data_dir="data")  # 只补缺失项，按天记日志
repair_log("manifest.json")                          # 值班查看每次修复的文件清单
export_diff("manifest.json", "diff.json", data_dir="data")     # 差异清单导出
export_manifest("manifest.json", "shard-0.json", shard=0)      # 按分片导出查看
```
