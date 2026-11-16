# xssreflect

安全方向的反射型 / 存储型 XSS 检测小工具，纯 Python 标准库。按回显所在
上下文判断编码后能否执行，而不是只看响应里有没有原样回显。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 解决的问题

旧实现只做原文匹配，导致两类误判：编码后的回显被当成没命中，解码后又被
当成命中，同一页面给出两种结论；存储型没有入口，只能人工逐个点。新引擎：

- 用 payload 的字母数字骨架定位**全部**反射点，原样、HTML 实体、URL
  百分号、双重/三重编码都能找到；同一参数在多个位置出现时**逐处判断**，
  不因为一处命中就跳过其余位置。
- 区分四种回显上下文，按上下文判断编码后能否执行：
  - `html`（HTML 正文）：尖括号未编码即可注入标签 → `hit`；实体或百分号
    编码后浏览器不解析为标签 → `safe`。
  - `attribute`（属性值）：能闭合定界引号、用空白断开无引号属性、或在
    事件处理属性里保留 JS 语法字符才 `hit`，否则 `safe`。
  - `script`（`<script>` 内部）：未转义的 `</script>` 或字符串定界符可
    闭合脚本块/字符串才 `hit`；反斜杠、`\uXXXX`、`\xXX` 转义后只是字符串
    数据 → `safe`。
  - `url`（href/src/action 等）：未编码的 `javascript:`、`vbscript:`、
    `data:text/html` 方案可执行 → `hit`；冒号被编码或为 http(s) 等普通
    导航方案 → `safe`；未知方案交 `review`。
- 编码过的内容**先还原再判断**，每条结论带完整 `restore` 还原过程
  （编码类型、还原前后片段）。
- 同一页面、同一上下文、同一种还原方式的重复回显**合并成一条**，
  `occurrences` 记次数，`offsets` 记全部位置。
- 拿不准的（残缺结构、多重/混合编码、未知 URL 方案）标记 `review`，
  留待人工确认，不直接给安全或命中结论。

## 入口与返回

- `load(page, body)`：登记页面正文（page 可用 URL 字符串作键）。
- `add_entry(page, param, stored=False, reason="")`：登记检测入口；
  `stored=True` 表示存储型入口，带 `reason` 表示该入口本次跳过。
  配套 `remove_entry` / `unload`。
- `check(url, param, payload=None)`：检测单个参数，返回
  `findings`（命中位置 `offset/offsets`、上下文 `context`、`payload`、
  回显原文 `raw`、还原结果 `decoded`、还原过程 `restore`、结论 `verdict`
  与理由 `reason`）、`uncovered`（未覆盖参数及跳过原因）、`parse_errors`
  （页面结构变化导致的解析失败，单列）。
- `check_all()`：对所有已登记入口逐页逐参数检测，存储型入口同样覆盖。
- `report()`：按页面汇总 `hit/safe/review` 条数、上下文分布、未覆盖项和
  解析失败页面，供运营按页面查看结论。
- `reset()`：清空默认引擎全部状态；也可直接实例化 `ReflectEngine`
  获得互不共享状态的独立引擎。

## 判定口径

- 结论只有三档：`hit`（该上下文下编码拦不住，可执行）、`safe`（回显存在
  但编码有效，无法执行）、`review`（拿不准，需人工确认）。
- HTML 注释里的回显不参与页面解析，判 `safe` 并在结论里标注 `in_comment`。
- 解析失败不丢弃反射点：结构残缺时整页兜底再扫一遍，按最深覆盖区域归类，
  同时把解析问题写进 `parse_errors`。
