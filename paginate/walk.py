"""翻页抓取：页码/游标双模式、滑动窗口去重、断点恢复、并发有序输出。"""

import json
import os
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

_UNSET = object()
_DEFAULT_RETRY_ON = (OSError,)  # 含 ConnectionError、TimeoutError、URLError
_ITEMS_KEYS = ("items", "data", "results")
_CURSOR_KEYS = ("next_cursor", "cursor")


def walk(fetch_page, *, start=_UNSET, state_path=None, concurrency=1,
         max_retries=3, backoff=0.1, retry_on=_DEFAULT_RETRY_ON,
         dedup_window=1000):
    """抓完全部页，按页序返回去重后的条目列表。

    fetch_page(token) 返回 list（简单页码模式，token 从 1 递增）或 dict：
    dict 取 items/data/results 为一页；含 next_cursor/cursor 走游标模式，
    否则按 page/next_page/first_page/total_pages 走页码模式。
    """
    walker = _Walker(
        fetch_page,
        start=start,
        state_path=state_path,
        concurrency=concurrency,
        max_retries=max_retries,
        backoff=backoff,
        retry_on=retry_on,
        dedup_window=dedup_window,
    )
    return walker.run()


def _key(item):
    try:
        hash(item)
    except TypeError:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, default=repr)
    return item


def _normalize(resp, token):
    """把响应规整为 (items, kind, next_token, page, total, first_page)。"""
    if resp is None:
        resp = []
    if isinstance(resp, dict):
        items = []
        for name in _ITEMS_KEYS:
            if name in resp:
                items = resp[name] or []
                break
        if any(name in resp for name in _CURSOR_KEYS):
            return items, "cursor", resp.get("next_cursor"), None, None, None
        page = resp.get("page", token)
        if "next_page" in resp:
            next_token = resp["next_page"]
        elif isinstance(page, int):
            next_token = page + 1
        else:
            next_token = None
        return items, "page", next_token, page, resp.get("total_pages"), resp.get("first_page")
    next_token = token + 1 if isinstance(token, int) else None
    return list(resp), "list", next_token, token, None, None


class _Walker:
    def __init__(self, fetch_page, *, start, state_path, concurrency,
                 max_retries, backoff, retry_on, dedup_window):
        self.fetch_page = fetch_page
        self.token = 1 if start is _UNSET else start
        self.state_path = state_path
        self.concurrency = max(1, concurrency)
        self.max_retries = max(0, max_retries)
        self.backoff = backoff
        self.retry_on = retry_on
        self.items = []
        self.seen = set()
        self.window = deque(maxlen=max(1, dedup_window))
        self.base = None   # 起始页码，以首个页码模式响应为准
        self.total = None  # 总页数，以最后一次响应为准
        self.done = False
        if state_path:
            self._load()

    def run(self):
        try:
            while not self.done:
                if self.concurrency > 1 and isinstance(self.token, int):
                    self._step_concurrent()
                else:
                    self._consume(self._fetch(self.token), self.token)
        except BaseException:
            self._save()
            raise
        self._clear_state()
        return self.items

    # ---------- 抓取 ----------

    def _fetch(self, token):
        attempt = 0
        while True:
            try:
                return self.fetch_page(token)
            except self.retry_on:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                time.sleep(self.backoff * (2 ** (attempt - 1)))

    def _step_concurrent(self):
        size = self.concurrency
        if self.total is not None and self.base is not None:
            size = max(1, min(size, self.total - (self.token - self.base)))
        tokens = [self.token + offset for offset in range(size)]
        futures = {}
        with ThreadPoolExecutor(max_workers=size) as pool:
            for token in tokens:
                futures[token] = pool.submit(self._fetch, token)
            for token in tokens:  # 按页序消费，与完成顺序无关
                self._consume(futures[token].result(), token)
                if self.done:
                    break

    # ---------- 消费 ----------

    def _consume(self, resp, token):
        items, kind, next_token, page, total, first = _normalize(resp, token)
        if kind == "page":
            if self.base is None:
                self.base = first if first is not None else page
                if isinstance(self.base, int) and isinstance(page, int) and self.base < page:
                    self._backfill(self.base, page)
            if total is not None:
                self.total = total
        if not items:  # 空页 -> 结束
            self.done = True
            return
        fresh = []
        for item in items:
            key = _key(item)
            if key not in self.seen:
                self._remember(key)
                fresh.append(item)
        if not fresh:  # 整页重复 -> 结束
            self.done = True
            return
        self.items.extend(fresh)
        if next_token is None:
            self.done = True
            return
        if kind == "page" and self.total is not None and isinstance(page, int):
            if page - self.base + 1 >= self.total:
                self.done = True
                return
        self.token = next_token
        self._save()

    def _backfill(self, first, upto):
        for token in range(first, upto):
            items, _, _, _, _, _ = _normalize(self._fetch(token), token)
            for item in items:
                key = _key(item)
                if key not in self.seen:
                    self._remember(key)
                    self.items.append(item)

    def _remember(self, key):
        if self.window.maxlen and len(self.window) == self.window.maxlen:
            self.seen.discard(self.window[0])
        self.window.append(key)
        self.seen.add(key)

    # ---------- 进度落盘 ----------

    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (FileNotFoundError, ValueError):
            return
        self.token = state["token"]
        self.items = state["items"]
        for key in state["window"]:
            self._remember(key)
        self.base = state["base"]
        self.total = state["total"]

    def _save(self):
        if not self.state_path:
            return
        state = {
            "token": self.token,
            "items": self.items,
            "window": list(self.window),
            "base": self.base,
            "total": self.total,
        }
        directory = os.path.dirname(os.path.abspath(self.state_path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    def _clear_state(self):
        if self.state_path:
            try:
                os.remove(self.state_path)
            except FileNotFoundError:
                pass
