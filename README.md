# corpusmin

安全方向的小工具，用来按覆盖贡献收敛模糊测试语料。

语料条目至少带 `id`；`project` 用来分项目，`size` 用来在同贡献时优先留小的，
`covers`（也接受 `coverage` / `regions`）声明这份语料命中的代码区域：

```python
{"id": "seed-01", "project": "pdf", "size": 120,
 "covers": ["parser.c:1-40", "xref.c:1-9"]}
```

没有覆盖字段的条目算“覆盖未知”，一律保留；`covers: []` 才是确认零贡献。

- `minimize(corpus)`：按覆盖贡献做确定性贪心收敛，只丢弃覆盖已被保留
  语料完全覆盖的条目，保留集合能复跑出同样的覆盖。返回保留/丢弃数量、
  覆盖前后变化、按区域的前后对比、唯一支撑区域（`solo_regions`）和被丢弃
  语料的 archive 去向。
- `report()` / `report(project)`：按项目查看收敛结果、archive 和按轮次
  保留的覆盖快照。
- `restore(id, project=None)`：从 archive 取回语料，连同它声明过的覆盖。
- `add(items)`：新增语料并记录每份语料新增的区域（不立即丢弃）。

运行：`python3 demo.py`，测试：`python3 -m unittest discover -s tests`
