# retention

按数据集配置的保留策略执行器，纯 Python 标准库。

## 策略字段（Dataset）

- name / root：数据集名与目录；days：保留天数。
- min_keep：过期文件中至少保留最新 N 份；exempt_tags：命中标签即豁免。
- time_field / tags_field：记录内字段名，默认 timestamp / tags。
- batch_size：确认执行时每批删除个数，默认 100。

## 执行口径

- 年龄只按记录内 time_field（epoch 秒或 ISO 8601）判断，不看文件时间；
  无法解析时间的文件一律保留（清单中记 no_time_field）。
- plan(datasets=[...], manifest_dir=...) 生成清单并落盘，旧清单保留 30 天。
- apply(plan, dry_run=True) 干跑不删文件；apply(plan, confirm=True) 才分批删除。
- 每次 apply 在 audit_dir（默认 ./retention_audit）写一条 JSON 审计记录。
- 兼容旧接口：plan(root, days) 按 mtime 返回路径列表，apply(paths) 直接删。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

