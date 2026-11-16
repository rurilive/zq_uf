# packerdetect

安全方向的样本加壳识别小工具。识别基于**结构和行为**，区段名只是信号之一，
改名样本靠区段结构、入口点位置、导入表和运行时行为判定，每条结论都带判定依据。

## 识别信号

- 区段名：命中已知家族为高置信，但可被改名绕过，不作为唯一依据；
- 区段结构：RWX 区段、可写且 raw_size=0 的空白解压区、高熵区段（熵 ≥ 7.0）；
- 入口点：是否落在最后一个区段、是否不在常规可执行区段（stolen bytes）；
- 导入表/行为：只剩 LoadLibrary/GetProcAddress 的最小导入集、运行时分配 RWX、
  自修改、动态 API 解析、反调试/反虚拟机等。

结论三档：

| packer     | 含义                                   | pending |
|------------|----------------------------------------|---------|
| 家族名     | 命中已知家族（high=区段名，medium=结构）| False   |
| `unknown`  | 有壳信号但对不上家族，附结构画像待人工 | True    |
| `unpacked` | 无加壳信号                             | False   |

## 入口

- `detect(sample)`：返回 `packer / confidence / evidence / structural / pending /
  pending_reason`，证据逐项列出，判定不了的标 `pending=True` 待人工。
- `diff(before, after)`：脱壳前后逐项比对——区段增减、入口点变化、导入表
  增删（`import_delta`）、行为变化，并对 after 重新识别，`clean` 表示是否脱干净。
- `unpack(sample, params=None)`：先 detect 再脱壳，成功返回脱后样本和 diff，
  失败保留 `original` 原样本与机器可读 `reason` + 中文 `reason_detail`。
- `get_unpack_params / set_unpack_params / UnpackParamStore`：脱壳参数按家族
  保存，同家族样本自动复用；`UnpackParamStore(path)` 支持 JSON 落盘跨运行复用。
- `families()`：已知家族及区段名特征；结构规则见 `packerdetect/signatures.py`。

## 样本格式

区段可以是字符串，也可以是带属性的字典（`characteristics / entropy /
raw_size / virtual_size / virtual_address`）；入口点支持十六进制字符串或整数；
`behavior` 为运行时行为列表，`payload` 可携带真实程序线索供脱壳重建。

## 验证

```
python3 -m unittest discover -s tests -v   # 20 个用例
python3 demo.py                            # 端到端演示
```
