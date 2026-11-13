"""固定间隔的请求节流。"""
import time


class Limiter:
    def __init__(self, interval=1.0, clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self.clock = clock
        self.sleep = sleep
        self.last = {}
        self.reports = []

    def acquire(self, site):
        now = self.clock()
        last = self.last.get(site)
        if last is not None:
            wait = self.interval - (now - last)
            if wait > 0:
                self.sleep(wait)
        self.last[site] = self.clock()

    def report(self, site, status):
        self.reports.append((site, status))
