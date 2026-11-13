from logsample import lines, reset, write

reset()
for index in range(5):
    write({"url": f"/{index}", "status": 200, "slow": index == 3})
print(len(lines()), "行")
print(lines()[3])

# TODO: 第二段演示（实现后补）
