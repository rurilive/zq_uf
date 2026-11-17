# packerdetect
安全方向的小工具，用来缩小崩溃样本。
`minimize(crash)` 逐步删减输入，每步验证崩溃仍可复现，失败即回退；
`reproduce(id)` 按编号取最小样本（最小样本优先）和复现命令。
运行：python3 demo.py；测试：python3 -m unittest discover -s tests
