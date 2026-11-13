"""任务耗时预测，当前用全局历史平均值。"""

SAMPLES = [120, 180, 240, 300]


def reset(samples=None):
    SAMPLES[:] = list(samples or [120, 180, 240, 300])


def predict(task, size):
    """不看任务类型和数据量，一律返回平均值。"""
    avg = sum(SAMPLES) / len(SAMPLES)
    return {"eta": avg, "low": avg, "high": avg}
