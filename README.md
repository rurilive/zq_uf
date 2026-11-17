# keyleak
安全方向的小工具，用来在仓库和配置里找疑似密钥。

判定依据不是单一关键字，而是三种形态证据综合：已知密钥前缀（AWS/GitHub/Slack/JWT/PEM 等）、
高熵字符串（香农熵 + 字符集多样性）、密钥类赋值上下文（token/secret/password 等），
占位符和模板变量（`${...}`、`{{ ... }}`、changeme 等）直接过滤。

## 运行
```
python3 demo.py
python3 -m unittest discover -s tests
```

## 入口
- `scan(paths, whitelist=None)`：扫描 `{仓库/文件: 内容}` 映射，不访问磁盘。
  同一密钥按内容合并成一条；`locations` 区分 code/config/build 三类位置；
  定义行之外的出现记入 `usage`，构建产物命中或多点出现时 `in_use=True`。
- `report()`：按仓库分组汇总，给出 open/resolved/in_use/whitelisted/duplicates。
- `add_whitelist(values)`：误报值或 entry_id 加入持久白名单，后续扫描自动排除。
- `resolve(entry_id, handled_by, method)`：记录处置人和方式，状态跨扫描保留。
