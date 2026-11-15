"""两站点试跑：一个长列表、一个短列表，都用页面反馈决定何时停。

模拟页面在滚动后先显示骨架屏、网络忙，等加载完成才渲染新条目；
长列表滚到下一页，短列表一次到底。最后演示失败留证与恢复续抓。
"""

from scrollload import StopStats, done, scroll


class Clock:
    """假时钟：sleep 推进时间，跑演示不用真等。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakePage:
    def __init__(self, site, url, views, delay=0.3, fail_on_scroll=None):
        self.site = site
        self.url = url
        self.views = views
        self.delay = delay
        self.fail_on_scroll = fail_on_scroll
        self.clock = Clock()
        self.index = 0
        self.scrolls = 0
        self.ready_at = 0.0
        self.error = None

    @property
    def loading(self):
        return self.clock() < self.ready_at

    @property
    def network_idle(self):
        return self.clock() >= self.ready_at

    @property
    def has_more(self):
        return self.index < len(self.views) - 1

    @property
    def at_end(self):
        return self.has_more is False

    @property
    def items(self):
        if self.loading:  # 加载中只给骨架屏，不能当数据
            return [{"id": f"skeleton-{i}", "skeleton": True} for i in range(3)]
        return self.views[min(self.index, len(self.views) - 1)]

    def scroll(self):
        self.scrolls += 1
        self.ready_at = self.clock() + self.delay
        if self.fail_on_scroll == self.scrolls:
            raise RuntimeError("element detached while scrolling")
        self.index = min(self.index + 1, len(self.views) - 1)

    def snapshot(self):
        return {"url": self.url, "scroll_y": self.scrolls * 900}


def cards(prefix, count):
    return [{"id": f"{prefix}-{i}", "title": f"{prefix} 商品 {i}"} for i in range(count)]


def run(page, **kwargs):
    return scroll(page, clock=page.clock, sleep=page.clock.sleep,
                  poll=0.05, **kwargs)


def show(label, result):
    print(f"{label}: 滚动 {result['rounds']} 次，新增 {result['added']} 条，"
          f"已见 {result['seen']} 条，停止原因 {result['reason']}，"
          f"骨架屏丢弃 {result['skeletons']} 条")


def main():
    long_page = FakePage(
        "长列表站", "https://long.example/list",
        [cards("long", (n + 1) * 10) for n in range(7)],
    )
    short_page = FakePage(
        "短列表站", "https://short.example/list",
        [cards("short", 4)],
    )

    stats = StopStats()
    for page in (long_page, short_page):
        show(page.site, run(page, stats=stats))

    print("done() 长列表站:", done(long_page), " 短列表站:", done(short_page))

    failing = FakePage(
        "失败站", "https://broken.example/list",
        [cards("broken", (n + 1) * 6) for n in range(5)],
        fail_on_scroll=2,
    )
    failed = run(failing, stats=stats)
    show("失败站（第一次）", failed)
    print("错误现场:", failed["errors"][0]["error"])
    print("现场快照:", failed["errors"][0]["snapshot"]["page"])
    print("已见条目尾部:", failed["errors"][0]["snapshot"]["seen_tail"][-3:])

    failing.fail_on_scroll = None
    failing.delay = 0.0
    resumed = run(failing, seen=failed, stats=stats)
    show("失败站（恢复后）", resumed)
    print("两次新增合计:", failed["added"] + resumed["added"], "（去重后不重复计数）")

    print("按站点的停止原因统计:")
    for site, entry in stats.report().items():
        print(f"  {site}: {entry['reasons']}")


if __name__ == "__main__":
    main()
