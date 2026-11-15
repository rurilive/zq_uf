"""告警：刷新失败时保留旧结果并发出告警。"""

import logging
from collections import deque
from threading import Lock

logger = logging.getLogger("dnscache2")


class Alerter:
    """记录告警事件并打日志；可注入 callback 接入监控系统。"""

    def __init__(self, callback=None, max_events: int = 200):
        self._callback = callback
        self._events: deque = deque(maxlen=max_events)
        self._lock = Lock()

    def alert(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)
        logger.warning("dnscache2 alert: %s", event)
        if self._callback is not None:
            self._callback(event)

    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events)
