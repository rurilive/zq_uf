"""抓取失败分类，当前一律重试。"""


def classify(error, response=None):
    """不区分原因，统一记成 error。"""
    return {"reason": "error", "evidence": str(error)}


def policy(reason):
    """所有原因都重试，预算按任务。"""
    return {"action": "retry", "budget": "task", "delay": 1}
