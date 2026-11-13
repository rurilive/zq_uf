# glossary

现状：按术语名覆盖保存，别名、版本和字段映射都没有。

问题：同一指标叫法不同，口径打架，字段变更后没人同步。

目标：术语和字段的映射中心，支持别名、跨表、改名历史和搜索。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

术语字段：`name`（唯一名）、`definition`、`aliases`、`abbreviations`、
`caliber`（统计口径）、`owner`、`source`、`valid_from`/`valid_to`（有效期）。
`define` 登记或更新术语；`lookup` 只认精确名；`search` 按别名、缩写和
描述搜索并返回匹配片段；`map_field` 把字段挂到术语上，字段可挂多个
术语、术语可跨表；`fields_of`/`terms_of` 双向查询映射。

版本口径：同名 `define` 后定义生效，旧版本进 `history` 可回查旧定义；
来源不同且定义冲突时记入 `conflicts`，双方来源都保留；`rename` 改名
后旧名和旧定义留在历史，字段映射自动指向新名；术语变更会向挂了该
术语的字段负责人发通知，见 `notifications`。
