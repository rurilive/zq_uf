# corpusmin
安全方向的小工具，按覆盖贡献收敛模糊测试语料。
语料条目带 id、project、coverage（覆盖的代码区域列表）。
minimize 按项目做贪心集合覆盖，只留带来新增覆盖的语料，
丢弃的进 archive（去向在返回值的 archive 字段），restore 可连覆盖一起取回。
report 按项目给出收敛结果、区域级覆盖对比和只靠一份语料撑着的区域，
每轮 minimize 的覆盖数据保留在 report 的 rounds 里。
运行：python3 demo.py；测试：python3 -m unittest discover tests
