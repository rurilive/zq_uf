# logmask

安全方向的资产版本识别小工具，纯标准库。
按服务和端口分别识别：响应头、页面特征、默认文件、协议行为多信号综合，
识别不出的标 unknown 并保留原始响应片段，汇总按主机分端口列出，不合并版本。

```python
from versionid import identify, override, register, report
register("10.0.0.8", 80, raw_response)   # 登记原始响应
identify("10.0.0.8", 80)                 # 服务/版本/依据/可信程度/raw
override("10.0.0.8", 80, "nginx", "1.18.0", note="人工确认")  # 推翻结论
report()  # hosts 按端口列出，unknown 按主机汇总待补采，distribution 看版本分布
```
