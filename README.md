# packerdetect
安全方向的小工具，用来缩小崩溃样本。

minimize(crash) 反复删减输入，每步验证崩溃仍在，失败就回退；
返回最小样本大小、删减比例、复现命令和原始样本位置，并记录每步删减区间。
删减比例过低时提示输入可能已经接近最小。原始样本和最小样本都保留，
reproduce(id) 以最小样本为准，按编号拿到能直接跑的输入。

运行：python3 demo.py
测试：python3 -m unittest discover -s tests
