# extractconf

配置驱动的字段抽取加载器，入口 \`load(path)\` 校验配置、\`apply(page, conf)\` 抽取。

## 配置结构

顶层 \`{"fields": {字段: 定义}}\`，定义含 \`rules\`（必填，按顺序回退）、
\`type\`（str/int/float/bool）、\`default\`、\`required\`。规则为
\`{"name", "type", "value"}\`，type 支持 \`css\`（选择器）、\`regex\`（首个分组）、
\`jsonpath\`（如 \`$.a.b[0]\`）。\`apply\` 返回 \`Result\`，\`result.matched\` 记录
每个字段命中的规则名。旧的 \`{"字段": "正则"}\` 映射仍兼容。

## 错误码

- E001 JSON 解析失败；E002 fields 非对象；E003 字段定义非对象
- E004 rules 为空；E005 规则缺 name/type/value 或类型未知；E006 正则编译失败
- E007 未知类型转换；E008 必填字段未命中；E009 类型转换失败

ConfigError 消息均带文件、字段和原因。

\`\`\`bash
python3 demo.py
python3 -m unittest discover -s tests
\`\`\`

