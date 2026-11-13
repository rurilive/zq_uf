# sheetload

业务表格导入：按 schema 解析 CSV，校验后分批提交。纯标准库。

\`\`\`bash
python3 demo.py
python3 -m unittest discover -s tests
\`\`\`

## 用法

\`load(path, schema)\`：schema 为列名列表时返回字符串行（旧行为，缺列补空串）；
为字典时走校验流程，返回报告
\`{sheet, dry_run, total, rows, batches, errors, conflicts, skipped_empty}\`。
\`errors\` 每项含行号（表头为第 1 行）和列名；\`path\` 为目录时每个 \`.csv\` 是一张工作表。

## schema 字段

- \`fields\`：列定义 \`{name, type, required, enum, min, max, merged, formats}\`，列乱序/多余列均可
  - \`type\`：\`str\`（默认）/ \`int\` / \`float\` / \`decimal\` / \`bool\` / \`date\` / \`datetime\`，转换失败记行号列名
  - \`merged: True\`：合并单元格展开，空格按上一行补齐；可选列空值为 \`None\`
- \`key\`：业务键（单列或列表），重复记入 \`conflicts\`
- \`on_conflict\`：\`error\`（默认，记错误并丢弃）/ \`skip\`（保留先到）/ \`overwrite\`（后者覆盖）
- \`sheet\`：工作表名字或序号（默认 0）
- \`formula\`：\`error\`（默认，\`=\` 开头单元格报错）/ \`keep\`（保留原文）
- \`constraints\`：跨列约束 \`[{left, op, right, message?}]\`，op 为 \`< <= > >= == !=\`
- \`dry_run: True\`：只出报告不提交；否则按 \`batch_size\`（默认 500）分批，
  可选 \`commit(batch)\` 回调逐批接收

