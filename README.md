# keyleak

安全方向的小工具，用来在仓库和配置里找疑似密钥。纯标准库实现，扫描过程不访问磁盘。

## 解决的问题

- 只靠关键字会把普通配置和占位符一起报出来，真正的高熵密钥反而漏掉；
- 同一个密钥散落在多个仓库，逐条确认是重复劳动；
- 泄露后不知道密钥是否还在被调用，直接删有业务中断风险。

## 判断依据（形态 + 上下文）

`scan()` 同时看三类信号，取最高可信程度：

1. **已知前缀**：AWS `AKIA`、GitHub `ghp_`、Slack `xoxb-`、OpenAI/Anthropic `sk-`、Google `AIza`、PEM 私钥块等，命中即 `high`；多前缀命中时取最特异（最长）的那个。
2. **高熵字符串**：Shannon 熵 + 长度 + 字符类别数联合判断，过滤版本号、路径、普通英文单词、单字符重复。
3. **上下文**：赋值/配置键带 `password`、`token`、`api_key` 等字样时值又不随机，报 `low` 交人工确认。

占位符（`${ENV}`、`{{ var }}`、`<...>`）和人尽皆知的示例值（`changeme`、`hunter2`）直接忽略。

## 合并、位置与使用痕迹

- 同一密钥按**内容**合并成一条（`id` 为内容的哈希），所有出现位置挂在 `locations`，并区分 `code` / `config` / `build`（构建产物优先级最高）。
- `usage` 区分 `definition`（赋值/配置定义）与 `call`（函数参数等引用）；存在调用或多处引用时 `in_use=true`、状态 `in_use`，单独标出，避免误删。
- 输出默认用 `masked` 打码，原值在 `value` 字段，便于按需取用。

## API

```python
from keyleak import scan, report, whitelist, triage, reset

result = scan({
    "web/app.py": 'token = "samevalue"\n',
    "ops/deploy.yml": "api_key = samevalue\n",
})
result["entries"]   # 合并后的密钥条目
result["repos"]     # 按仓库的文件计数

report()            # 处置看板：total/open/in_use/resolved/whitelisted/duplicates/repo_groups

whitelist(value="...")          # 误报按精确值加白，对已扫结果立即生效
whitelist(pattern=r"sk-.*")     # 或按正则
triage(entry_id, handler="alice", method="rotated", note="...")  # 记录处理人和方式
reset()             # 清空内存态
```

状态枚举：`open`（待处理）、`in_use`（仍在使用）、`resolved`（已确认处理）、`whitelisted`（误报）。

## 运行与测试

```
python3 demo.py
python3 -m unittest discover -s tests
```
