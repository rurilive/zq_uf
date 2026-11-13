# multisource

多源数据对账：按业务键对齐 N 份数据，输出缺失、重复、字段差异，并按来源和类型归因，结果可 JSON 序列化、可重放复现。

## 运行

    python3 demo.py
    python3 -m unittest discover -s tests

## 接口

reconcile(sources, config)，sources 为至少两份行字典列表。默认 legacy 模式只比前两份（兼容旧断言）；config 含 "mode": "multi" 或 "source_names" 时启用多源模式。

## 对齐键与参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| key | "id" | 业务键字段，所有来源按它对齐 |
| source_names | source_0..N | 来源命名，数量须与 sources 一致且不重复 |
| tolerance | 无 | 数值容差阈值（非负），abs(a-b)<=tolerance 视为一致 |
| conversions | [] | 字段级口径转换，见下 |

conversions 每项为 {"field", "op", "sources"?}：sources 限定生效来源（缺省全部）；op 支持 scale/divide（配 factor）、round（配 ndigits）、int/float/str/upper/lower/strip。

## 归因规则

- missing：键在部分来源缺失，归因到 missing_from 列出的来源。
- duplicate：同一来源内键重复，归因到该来源，count 为出现次数，比较取首行。
- value：同字段跨来源取值（经口径转换后）不一致。多数来源一致的值为 consensus，归因到偏离共识的来源；无多数派时 consensus=None，归因到所有持有该键的来源。

## 报告与下钻

multi 模式返回 {mode, key, sources, config, diffs, report}。report.total/by_type/by_source 是汇总，report.by_key 把业务键映射到 diffs 下标实现下钻；每条 diff 带 attribution: {type, sources}。异常（来源不足、缺键、命名冲突、未知 op/模式、负容差、转换失败）一律抛 ValueError，不静默跳过。
