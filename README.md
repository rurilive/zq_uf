# extractcache

带版本和容量的抽取规则编译缓存，纯标准库，接口 `compile(rules)` / `invalidate(version)`。

缓存与失效口径：
- 缓存键 = 规则指纹 sha256(规则 id + version + params)，规则或参数变更即换键；未声明 version 的规则不缓存，每次重新编译（兼容旧行为）。
- `invalidate(version)` 删除该版本全部条目，并写入共享失效日志广播；其他进程下次读取时懒清理。编译失败（规则非法）不写缓存。
- 容量按条数限制（`EXTRACTCACHE_CAPACITY`，默认 128），淘汰最久未用：`last_access + 命中次数 × 60s` 最低者先出。
- 多进程共享：缓存落盘（`EXTRACTCACHE_HOME`，默认 `~/.extractcache`），fcntl 文件锁保护，临时文件 + `os.replace` 原子更新。
- `stats()` 输出命中、失效、淘汰和命中率，命中率按规则统计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
