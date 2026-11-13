# langdetect

纯 Python 标准库的页面语言识别，支持中/英/日/韩/法/德，接口为
`detect(text, config=None)`，`config` 可覆盖 `DEFAULT_CONFIG` 中的阈值与权重。

## 打分规则

- 字符区间：假名→日文、谚文→韩文（强信号）；汉字→中文为主、日文少量；
  拉丁字母→英/法/德（弱信号）；é/à/ç 等→法文，ä/ö/ü/ß→德文。
- 常见词/停用词：拉丁语按词表命中加分，中日韩按特征词/助词子串命中加分，
  词命中权重 `word_weight` 高于单字符 `char_weight`。
- 置信度 = 最高分 /（最高分 + 次高分）；短文本（< `short_text_length`）
  再乘 `short_text_confidence_scale` 折算。

## 阈值与输出

- 置信度低于 `min_confidence`（短文本用 `short_text_min_confidence`）时
  标记 `low_confidence=True`，结果保留不丢弃，路由层可自行调阈值。
- 返回 `lang`、`confidence`、`low_confidence`、`mixed`、
  `segments`（逐段语言与置信度）、`distribution`（每种语言的段落数与平均置信度）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
