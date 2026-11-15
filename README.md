# urlnorm2

URL 等价类的**增量**维护模块，纯 Python 标准库。接口保持 `add(url)` / `same(a, b)`。

## 增量口径

- `add(url)` 只做一次归一化并查 key 索引：key 命中即并入已有类（`action="merged"`），否则新建（`"created"`），重复 URL 为 `"existing"`。**不再全量重建**，已有等价关系不会丢失。
- `same(a, b)` 按当前规则管道逐步归一化，返回 `SameResult`：可直接当 bool 用，`.rule` / `.rule_version` 给出首次令两者相等的规则；原始串相等时 `.rule is None`。未入队的 URL 也能查。
- 规则是带 `id` + `version` 的有序管道（`urlnorm2.rules`，内置 `strip_fragment` / `trim_trailing_slash` / `lowercase`）。`update_rule` 替换规则后做**局部重算**：全量检测 key 变化，但只有受影响类进入影子重建，重建结果原子切换，`recompute(progress=...)` 回调期间 `class_of`/`size` 等查询读到的始终是切换前的一致快照。重算天然支持类的**合并与拆分**，报告里给出 `splits` / `merges` / `recomputed_class_ids`。
- 失效链接用 `remove` / `remove_many` 清理：成员清空的类被删除，代表失效时顺延到剩余成员中最早出现者。`size()` / `class_info()` / `stats()` 查看类规模、成员和规则依赖。
- `save(path)` 原子写 JSON 快照（临时文件 + `os.replace`），`URLNormIndex.load(path, rules=...)` 恢复：校验全部 key，规则版本漂移时只重算受影响类，恢复细节在 `index.recovery`。

## 代表口径

每条 URL 首次出现时分配单调递增序号；**类代表 = 成员中序号最小（最早出现）的原始 URL**。并入、合并、拆分、落盘恢复都不改变代表，仅当代表本身被 `remove` 时才顺延。代表存的是原始 URL，不随后续规则归一化而改写。拆分时原代表所在新簇复用旧类 id。

## 用法

```python
from urlnorm2.index import URLNormIndex
from urlnorm2.rules import make_strip_query_rule

idx = URLNormIndex()
idx.add("https://shop/p/1")
idx.add("https://Shop/p/1/")          # merged，代表仍是 https://shop/p/1
idx.same("https://Shop/p/1/", "https://shop/p/1").rule  # 'lowercase'

idx._normalizer.add_rule(make_strip_query_rule(["utm_source"]))
report = idx.recompute()              # 只重建 key 变化的类，含 splits/merges
idx.remove("https://shop/p/1#x")      # 失效清理
idx.save("index.json")
restored = URLNormIndex.load("index.json", rules=...)  # 可恢复
```

模块级 `urlnorm2.add/same/remove/size/save/load/...` 操作一个全局索引；多实例或显式生命周期管理用 `URLNormIndex`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
