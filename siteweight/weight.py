"""模块级默认调度器：无反馈时等权轮转，反馈累计后动态调权。"""

from .scheduler import Scheduler

SITES = ["shop", "news", "blog"]
_scheduler = Scheduler(SITES)


def reset():
    global _scheduler
    _scheduler = Scheduler(SITES)


def next_site():
    """从默认调度器取下一个站点。"""
    return _scheduler.next()


def feedback(site, result):
    """向默认调度器反馈结果，返回该站点的新权重。"""
    return _scheduler.feedback(site, result)
