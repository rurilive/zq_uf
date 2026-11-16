# ssrfverify

安全方向的 SQL 注入检测小工具。按参数归并结论，不按固定 payload 逐条计数。

## 判定方式

- **报错型**：按 MySQL / PostgreSQL / MSSQL / Oracle / SQLite / Access / DB2 分开匹配特征，
  基线页面里已有的报错不算注入。
- **布尔盲注**：比较真条件、假条件响应与基线页面的差异；正常页和异常页分别记录。
- **时间盲注**：比较异常 payload 与基线的响应耗时（秒或毫秒均可登记）。
- 证据不足的标 `pending`（待确认）；同一参数出现多种结论时取置信度最高的一条，
  其余放进 `alternatives`。

## 入口

```python
from sqldetect import check, load, report, mark_false_positive

load(url, responses)          # 登记每个参数的多次尝试
out = check(url, params)      # 每个参数一条结论
rep = report(url)             # 按接口汇总；report() 给全部接口
mark_false_positive(url, param, reason)
```

`load` 的每个参数值可以是响应正文、trial 字典、trial 列表，或按
`baseline / true / false / timed` 命名的字典：

```python
responses = {
    "id": [{"payload": "'", "body": "...MySQL syntax..."}],
    "name": {
        "baseline": {"body": "<正常页>", "elapsed": 0.12},
        "true":  {"payload": "' OR '1'='1", "body": "<正常页>"},
        "false": {"payload": "' OR '1'='2", "body": "<空结果页>"},
    },
    "sort": {"baseline": {"body": "...", "elapsed": 0.2},
             "timed": {"payload": "1' AND SLEEP(5)--", "body": "...", "elapsed": 5.3}},
    "slow": {"skip": "timeout"},          # 超时 / 拒绝 / 协议不支持
}
```

`check` 返回可疑参数、判定依据、请求样例和复现步骤；未覆盖的参数列在 `uncovered`，
跳过原因固定为「超时」「拒绝」「协议不支持」。`report` 的 `duplicates` 恒为 0：
同一参数只保留一条结论。误报可标记并带原因。

运行 `python3 demo.py` 看完整输出，`python3 -m unittest discover -s tests` 跑测试。
