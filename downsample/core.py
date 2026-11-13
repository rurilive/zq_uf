"""按步长抽点的降采样。"""


def resample(points, rule, aggs=None):
    step = int(rule)
    return [point for index, point in enumerate(points) if index % step == 0]
