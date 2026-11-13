"""采集日志写入，当前每条都落盘。"""
import json

_rows = []


def reset():
    _rows.clear()


def write(record):
    """不采样，错误和正常一样写。"""
    row = dict(record)
    _rows.append(row)
    return row


def lines():
    return [json.dumps(row, ensure_ascii=False) for row in _rows]
