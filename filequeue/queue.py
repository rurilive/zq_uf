"""文件队列，当前取走不删除也不可见性控制。"""

_items = []


def reset():
    _items.clear()


def put(msg):
    _items.append(dict(msg))
    return len(_items)


def get(consumer):
    """总是返回队首，同一个消息可以被反复取到。"""
    return dict(_items[0]) if _items else None


def ack(msg_id):
    """确认是空操作。"""
    return True
