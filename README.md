# langdetect2

采集页面语言识别：从"只看响应头、缺省按英文"升级为**分层证据 + 段落级识别**。

## 识别流程（证据分层）

1. **响应头** `Content-Language` 与 **HTML 声明**（`lang`/`xml:lang`、`meta http-equiv`）先收集，只作声明；
2. 正文按**段落**做 Unicode 字符脚本归类（假名/谚文/汉字/拉丁/西里尔/阿拉伯……）；
3. 拉丁、西里尔等一对多脚本再用**常用功能词**区分具体语言（en/de/fr/es/it/pt/nl/tr、ru/uk）；
4. 声明与正文交叉验证，冲突时以正文为准并标记 `declaration_conflict`；
5. 按字母量加权汇总各语言占比，输出主语言、把握程度和原文样本。

## 把握程度与入库分流

- `high`：主语言占绝对优势（≥0.85）且证据强，可直接入库；
- `medium`：混排页、声明冲突但正文充分、声明兜底等，转人工复核；
- `low`：证据不足、无主语言、脚本无法映射，**不直接入库**，必须人工覆盖。
- 人工覆盖（`apply_override`）只改最终采用值，原识别结果完整保留在 `override` 字段。

## 返回字段

`detect(page)` 返回：`language`（主语言）、`ratio`（各语言占比）、
`confidence`（high/medium/low）、`samples`（原文样本，截断不改写）、
`paragraphs`（段落级语言/占比/样本）、`declared_languages`、
`declaration_conflict`、`multilingual`、`site`。

页面输入支持 `text`（按空行/换行分段）、`paragraphs`（已分段）、`html`
（自动解析块与 lang 属性），均可选带 `headers`、`url`。

## 新语言站点接入

开量前先用 `evaluate_onboarding(samples, expected_language=...)` 跑一批样本：
样本量 ≥20、主语言占比 ≥0.9、低把握率 ≤0.1、声明冲突率 ≤0.05 才判定 `ready`。
统计用 `SiteStats` 按站点汇总（语言分布、置信度分布、低把握率、覆盖率、混排率）。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

无第三方依赖，仅用 Python 标准库。
