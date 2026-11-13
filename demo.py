from downsample import resample

POINTS = [{"ts": i, "v": i} for i in range(6)]
print(resample(POINTS, 2))

# TODO: 第二段演示（实现后补）
