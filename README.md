# fieldlineage

字段级血缘：record 落字段边，trace 查表级，trace_field 查字段级上下游与层级。
纯 Python 标准库，无第三方依赖。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口与参数

- `record(job)`：job 为 dict，允许键 `job/inputs/outputs/columns/sql/renames`，未知键报错。
  - `columns`：`[{"from": "表.字段", "to": "表.字段"}]` 显式字段映射。
  - `sql`：`INSERT INTO ... SELECT ...`，解析出字段边、别名与函数。
  - `renames`：`[{"table","from","to"}]` 或 `[{"from": "表.旧", "to": "表.新"}]`，须同表。
- `trace(table, field=None)`：表级查询；field 命中时返回该字段链路经过的任务行，未命中回退表级。
- `trace_field(table, field, max_depth=10)`：上下游字段与层级；字段不存在抛 UnknownFieldError。
- `fields(table=None)` 列已知字段；`reset()` 清空全部状态。
- 阈值：`DEFAULT_MAX_DEPTH = 10`（fieldlineage/lineage.py），限制递归层数防环，可按调用覆盖。

## 节点与边

- 节点：字段全名 `表.字段`；改名后旧名进 `aliases` 历史，查询自动解析到新名。
- 边字段：`from`/`to`（字段全名）、`job`、`expression`、`functions`、`kind`、`alias`。
- `kind`：`direct` 直传 / `expression` 表达式 / `aggregation` 含聚合 / `rename` 改名。
- 聚合函数集合：`COUNT SUM AVG MIN MAX STDDEV VARIANCE MEDIAN`，出现在表达式即记为 aggregation。
- `count(*) as cnt` 这类无来源字段的输出不建边，但 expression/functions 仍记录在节点属性里。

## SQL 支持与错误

- 支持：别名（AS 或隐式）、多表 FROM、JOIN ... ON ...、表达式、聚合、CASE WHEN、窗口 OVER、CAST。
- 不支持即抛 SQLParseError：`select *`、`t.*`、子查询、UNION、缺 FROM、多条语句、非 INSERT 语句。
- 多来源表时裸列（无表名限定）无法定位，抛 SQLParseError，请写成 `表.字段`。
- 格式非法抛 JobFormatError：未知键、引用未声明的表、表达式缺别名、改名目标已存在等。
- 所有失败路径显式抛错，不静默跳过；异常基类 `LineageError`（fieldlineage/errors.py）。

