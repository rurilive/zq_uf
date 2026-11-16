# pcapflow

安全方向的审计日志完整性小工具，纯标准库。

每条记录存 `digest`（覆盖本条内容、前一条摘要 `link`、day/time、算法名）和 `link`，
形成逐条可验证的哈希链；`verify(span)` 只重算目标区间，支持序号/单天/跨天，
`limit` + 返回的 `resume` 令牌可中断续算，断链时给出前后两条记录、时间、原因和影响范围；
算法名在每条记录上，`set_algorithm` 迁移后旧区间仍按旧算法验证。

```
python3 demo.py
python3 -m unittest discover -s tests
```
