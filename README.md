# shardwrite

现状：所有采集结果追加到一个 JSONL 文件，按日期过滤读取。

问题：文件变大后打开慢，崩溃留半行，重跑新旧混在一起。

目标：分片加清单、原子改名、校验和与范围读取。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
