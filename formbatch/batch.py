"""批量表单提交，当前逐条提交且失败整批重来。"""

_sent = {}


def submit(forms):
    """每次调用都把所有表单重新提交一遍。"""
    rows = list(forms)
    results = [{"index": index, "ok": True} for index, _ in enumerate(rows)]
    _sent["last"] = results
    return {"batch": "last", "results": results}


def resume(batch_id):
    """恢复没有记录可读，返回空结果。"""
    return {"batch": batch_id, "resumed": False, "results": []}
