# sheetload

业务表格导入，纯标准库，接口 load(path, schema)。

- schema 为列名列表：旧行为，返回 dict 列表（缺列补空串）。
- schema 为 dict：校验流程，返回 Report（rows/errors/blank_rows/conflicts/batches/committed）。

schema 字段：

- sheet：工作表名或序号；path 为目录时每个 CSV 是一个工作表。
- columns：name、type(str/int/float/decimal/date/bool)、required、enum、range、fill_down(合并单元格按上一行补齐)。
- key：业务键列；on_conflict：skip(默认，保留首条)/overwrite(后者覆盖)/error(记错误)。
- formulas：error(默认，遇公式报错)/value(简单算式求值)。
- constraints：跨列约束，如 {"expr": "结束 >= 开始", "message": "结束早于开始"}。
- dry_run：只校验出报告不提交；batch_size：正式导入分批大小(默认 500)。

空行跳过并计数；类型与校验错误记录行号和列名；列顺序可变、多余列忽略。

运行：

    python3 demo.py
    python3 -m unittest discover -s tests
