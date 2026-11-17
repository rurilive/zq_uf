# alertfold

蜜罐访问行为聚类工具。一天几十万条访问里绝大多数是批量扫描，按来源地址
统计会得到几万个看似独立的来源；本工具改为**按行为聚类**，把请求路径、
时间间隔、请求头和工具指纹相似的来源合并成一个扫描组，让针对性探测
不再被批量扫描的噪音淹没。纯标准库实现，无外部依赖。

## 入口

```python
from honeypot import cluster, report, reset

result = cluster(events)   # 行为聚类，返回扫描组与离群事件
report()                   # 按事件量排序的组、离群、单地址组、按天汇总
reset()                    # 清空模块级状态与内存中的人工调整
```

`events` 是事件字典列表，每条至少含 `ip` / `path` / `at`（时间戳秒），
可选 `ua`、`headers`（字典）、`day`（字符串，缺省时按时间戳换算 UTC 日期）。

## 聚类怎么做

1. **行为画像**：同一地址的事件汇总成画像——路径频次、UA 工具指纹
   （nikto、sqlmap、masscan、gobuster 等 20+ 种）、请求头名集合、
   相邻请求间隔中位数、首次/末次时间。
2. **代表吸收制聚类**：工具指纹做硬阻塞，新画像只与同特征块内候选组的
   固定代表画像比较加权相似度（路径 0.40 / 工具 0.35 / 请求头 0.15 /
   间隔 0.10，阈值 0.60）。代表是固定小集合，单次判定成本恒定，
   50 万级事件也能近线性完成。
3. **离群事件**：参照同组其他成员的特征并集，事件的路径/工具/请求头
   所有同伴都没出现过、或间隔超过自身中位 10 倍的，标为离群。
   离群事件仍计入组统计，不会丢。
4. **单地址组**：无法归入任何组的来源进 `singletons`，通常就是值得
   单独跟进的针对性探测。

## 输出结构

`cluster()` 返回：

| 字段 | 含义 |
| --- | --- |
| `groups` | 扫描组列表，按事件量降序 |
| `groups_count` / `sources` / `events` | 组数、来源地址数、事件总数 |
| `outliers` | 扁平化的离群事件（含所属组 id 与离群原因） |
| `singletons` | 单地址组的 id 列表 |

每个组含 `id`、`label`、`signature`（跨轮稳定的特征哈希）、`sources`、
`events`、`first`、`last`、`features`（路径/工具/请求头条目及核心标记、
间隔统计）、`members`（地址明细，含 `also_in` 跨组关系）、`outliers`、
`relations`（本组成员与其他组的共享特征）、`daily`（按天汇总）。

同一地址跨多组行为时按主要行为归入一组，其余关系保留在成员的 `also_in`
和组的 `relations` 明细里，不重复计归属。

## 人工调整并用于下一轮

```python
from honeypot import adjust, save_adjustments, load_adjustments

adjust("merge", sources=[sig_b], into=sig_a, label="web-scanner-crew")
adjust("pin",   ip="203.0.113.99", group="web-scanner-crew")
adjust("label", group=sig_a, label="daily-nikto-crew")
save_adjustments("adjustments.json")

# 下一轮（可跨进程）
load_adjustments("adjustments.json")
cluster(events)          # 自动套用，或 cluster(events, adjustments="...json")
```

组引用可以是组 `id`、`signature` 或人工标注名。**跨轮引用优先用
signature 或标注名**——组 id 随规模重排，成员变化后 signature 也会变，
人工名最稳定。撤销用 `unmerge` / `unpin` / `unlabel`。

## 按天汇总

每个组的 `daily` 给出该组每天的事件数与来源数；`report()["daily"]`
是全局视角（每天多少组、多少来源、多少事件），用来观察批量扫描的变化。

## 演示与测试

```bash
python3 demo.py                                  # 端到端演示
python3 -m unittest discover -s tests -v         # 测试（15 个用例）
```
