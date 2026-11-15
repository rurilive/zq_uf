"""刷新执行器：默认线程池后台刷新，测试可换同步/手动执行器。"""

import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor


class InlineExecutor:
    """同步执行：刷新在当前线程内立即完成。"""

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)
        return None


class ManualExecutor:
    """手动执行：刷新任务排队，run_pending 时才执行，便于确定性测试。"""

    def __init__(self):
        self._queue = deque()

    def submit(self, fn, *args, **kwargs):
        self._queue.append((fn, args, kwargs))
        return None

    def run_pending(self):
        ran = 0
        while self._queue:
            fn, args, kwargs = self._queue.popleft()
            fn(*args, **kwargs)
            ran += 1
        return ran

    def pending(self):
        return len(self._queue)


class ThreadRefreshExecutor:
    """真实场景使用的线程池后台执行器。"""

    def __init__(self, max_workers: int = 8):
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="dnscache2-refresh"
        )
        self._futures = set()
        self._lock = threading.Lock()

    def submit(self, fn, *args, **kwargs):
        future = self._pool.submit(fn, *args, **kwargs)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(lambda f: self._discard(f))
        return future

    def _discard(self, future):
        with self._lock:
            self._futures.discard(future)

    def join(self, timeout=None):
        with self._lock:
            futures = list(self._futures)
        for future in futures:
            future.result(timeout=timeout)

    def shutdown(self):
        self._pool.shutdown(wait=True)
