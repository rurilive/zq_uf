# filerouter

按规则把采集文件分发到 `目标根/<日期>/<来源>/<规则名>/`，重名自动加 `-1`、`-2` 序号，不覆盖。

## 规则语法

规则是 dict 列表，按 `priority` 从大到小（缺省 0，同级保持书写顺序）依次匹配，一条规则的所有条件同时满足才算命中：

- `name`（必填）：规则名，命中后写进目标路径
- `suffix`：后缀，`"log"` 或 `["zip", "gz"]`
- `prefix`：文件名前缀
- `min_size` / `max_size`：文件字节数
- `contains`：文件头 64KB 内必须包含的字符串

`route(path, target_root, rules=None, source=None, date=None)`：`rules` 缺省用内置后缀规则，也可用 `load_rules("rules.json")` 从 JSON（`{"rules": [...]}`）加载；`source` 缺省取文件所在目录名，`date` 缺省取当天。

## 目录约定

- 命中：`<target_root>/<YYYY-MM-DD>/<来源>/<规则名>/<文件名>`
- 失败（无规则命中或读写出错）：移入 `<target_root>/_pending/`，同名 `.reason` 文件记录原因和来源
- `retry_pending(target_root, rules=None)` 只重跑待处理目录，成功后删除 `.reason`

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
