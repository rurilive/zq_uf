# extractcache

现状：每条记录都重新编译规则，结果不缓存。

问题：规则更新后缓存不失效谈不上，缓存也没有容量上限。

目标：按规则指纹缓存编译结果，版本失效，按条数淘汰。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
