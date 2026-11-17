# webshell

终端基线核查小工具：检查项按系统（Linux/Windows）和版本登记适用范围，缺项标为不适用，不再算未通过。

- `check(host)`：核查一台终端，host 为 `{"host", "os", "version"?, "items"}`；每项登记适用系统、期望值、实际值、判定，不适用项带原因单列，并回溯该终端上一次结果。
- `check(host, recheck=True)`：只复验该终端上一次未通过的项，返回首次与本次结果对比（fixed / still_failing / not_applicable）。
- `report()`：汇总总量、按系统与检查项交叉的结论；通过率分母只含适用项；附整改清单与复验对比。
- `report(host)`：按终端查看明细与历次核查记录（按时间留存）。
- `export_remediation(path)`：把未通过项导出为整改清单 CSV（终端、检查项、期望值、实际值、起始时间）。
