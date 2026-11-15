# linkrewrite

采集链接重写模块，纯 Python 标准库。按**站点**配置查询参数的保留、删除和注入，
追踪参数与分页参数分类处理，规则冲突按优先级仲裁，每次重写都留记录、规则变更可回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

```python
from linkrewrite import rewrite, rules

rewrite(url, site)                 # 重写，返回 before/after、命中规则、冲突与变更
rules(site)                        # 查看站点规则、静态冲突和当前修订版本
```

需要隔离配置时可自建 `Registry` 并传 `registry=` 参数：

```python
from linkrewrite import Registry, Rule, history

reg = Registry()
reg.configure_site("mysite", tracking=["camp_id"])   # 站点扩展参数表
reg.add_rule("mysite", Rule(id="strip", priority=100,
                            tracking={"delete": ["camp_id", "utm_source"]}))
rewrite("https://m/p?camp_id=1&q=2", "mysite", registry=reg)
history(site="mysite", registry=reg)                  # 重写记录（含未命中）
```

## 参数分类

查询参数先分类、再按类别独立处理，互不串扰：

| 类别 | 内置参数 | 说明 |
| --- | --- | --- |
| `tracking` | 所有 `utm_` 前缀、`gclid`、`fbclid`、`spm`、`from` 等 | 追踪/投放参数，可用 `configure_site(tracking=[...])` 扩展 |
| `pagination` | `page`、`offset`、`limit`、`cursor`、`page_size` 等 | 分页参数，可用 `configure_site(pagination=[...])` 扩展 |
| `other` | 其余全部 | 业务参数 |

同一名字同时进两张扩展表时按追踪参数处理。

## 规则声明

规则按站点注册，三个参数类别各自声明三种动作：

```python
Rule(
    id="shop-pagination",        # 站点内唯一
    priority=50,                 # 数字越大越优先
    path=r"^/list/",             # 可选：路径正则（re.search），缺省匹配全部路径
    description="分页规范化",
    tracking={"delete": ["utm_source"]},
    pagination={
        "retain": ["page"],                                   # 白名单：仅保留名单内参数
        "inject": {"page_size": {"value": "20",              # 注入：静态值或带校验的字典
                                 "min": 1, "max": 100}},
    },
    other={"retain": ["sort"]},
)
```

- **retain（保留）**：白名单。任一命中规则声明了 retain 即进入白名单模式，
  未被高优先级规则显式决定、又不在白名单内的参数会被删除；白名单跨类别合并。
- **delete（删除）**：删除指定参数。
- **inject（注入）**：参数不存在则补值，已存在则覆盖，并在 `changes.changed`
  里记录前后值。注入值支持四种校验：`allowed` 枚举、`pattern` 正则、
  `min`/`max` 整数区间，以及标量类型检查；**非法规则在 `add_rule` 注册时即抛
  `InvalidRule`，不会产生半成品配置**。
- 同一规则内，一个参数不能同时出现在 retain/delete/inject 中（注册时报错）。

## 优先级与冲突仲裁

1. 规则按 `priority` 降序匹配；**同优先级按注册先后**（先注册者胜）。
2. 同一参数被多条**可能同时命中**的规则显式声明（retain/delete/inject）即构成冲突，
   高优先级规则的动作生效，其余声明忽略。路径互斥（如 `^/article/` 与 `^/section/`）
   的规则不可能作用于同一 URL，不计冲突。
3. `rules(site)` 返回静态冲突（含人可读的 `reason`，说明谁胜出、为什么）；
   `rewrite()` 的结果只返回与当前 URL 实际相关的冲突。
4. 未被任何规则显式提及、且没有白名单约束的参数原样保留。

## 重写结果与记录

`rewrite()` 返回：

```text
before/after   重写前后完整 URL；未命中或无实际变更时 after 与 before 完全相同
hit            命中的规则 id、优先级及每条规则实际删除/保留/注入的参数
conflicts      本次 URL 上实际发生的冲突及仲裁解释
changes        按类别记录 removed（含原值）、injected、changed（前后值）
matched/revision/timestamp/seq   是否命中、规则版本、时间与记录序号
```

每次调用（包括未命中原样返回）都写入注册表的记录，可用 `history(site=, limit=)`
按站点过滤查询。重写是规则配置的纯函数：同一 URL 多次重写结果一致（幂等），
输出参数顺序确定（原序保留，新注入参数按名排序追加）。

## 版本与回滚

每次 `add_rule` / `remove_rule` / `configure_site` 都为站点生成一个不可变修订版本：

```python
rev = reg.current_revision("shop")
reg.add_rule("shop", new_rule)
reg.rollback("shop", rev)     # 恢复到历史版本；回滚本身也生成新版本，历史不丢
reg.rollback("shop", 0)       # 清空站点配置
reg.revisions("shop")         # 查看全部修订号
```

## 模块结构

```text
linkrewrite/
  classifier.py   参数分类（tracking / pagination / other）
  rules.py        Rule 模型与注入合法性校验（InvalidRule）
  registry.py     站点注册表、修订版本、重写记录
  engine.py       匹配、冲突仲裁、重写执行
  rewrite.py      对外门面 rewrite() / rules() / history() 与内置站点配置
```
