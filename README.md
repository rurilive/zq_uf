# alertfold

蜜罐访问归类小工具，按行为把访问记录聚成扫描组。

批量扫描换地址继续时不再被当成新来源：请求路径、时间间隔、
请求头/工具特征相似的来源合并成一组，组内记录地址数量和首次/末次时间，
偏离组特征的事件单独标为离群，针对性探测可以单独跟进。

## 用法

```python
from honeypot import cluster, report

result = cluster(events)   # events: [{"ip", "path", "at", "ua"?}, ...]
```

`cluster(events, prior=None, adjust=None)` 返回：

- `groups`：扫描组，含 `features`（路径模式、工具、间隔）、
  `sources`/`source_count`、`first`/`last`、`count`
- `outliers`：偏离组特征的事件及原因
- `daily`：按天汇总的事件数、来源数、活跃组数

同一地址跨多组出现时按主要行为（事件数最多）归入一组，
其余关系保留在该组的 `shared` 明细里。

分组结果保留提取出的特征，人工可调整并带入下一轮：

- `prior=上一轮结果`：特征相同的组沿用原组 id
- `adjust={"merge": [[idA, idB]], "move": {ip: 组id}}`：合并组或改归地址

`report()` 返回上一轮结果（含按天汇总）。

## 测试

```
python3 -m unittest discover -s tests
```
