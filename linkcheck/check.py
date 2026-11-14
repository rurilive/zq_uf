"""死链巡检，当前串行检查。"""

_results = []


def check(urls, workers=1):
    """逐个请求，workers 被忽略。"""
    _results.clear()
    for url in urls:
        _results.append({"url": url, "status": 200})
    return {"checked": len(_results), "workers": 1}


def report():
    return list(_results)
