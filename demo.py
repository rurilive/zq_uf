"""演示：普通页码翻页，以及游标过期后的回首页恢复 + 断点续抓。"""

import os
import tempfile

from apipage import (
    CursorExpired,
    load_checkpoint,
    save_checkpoint,
    walk,
)

# 第一段：旧的 next_page 页码翻页仍然可用
PAGES = {1: {"items": ["a"], "next_page": 2}, 2: {"items": [], "next_page": None}}
print("页码翻页:", walk(lambda p: PAGES[p]))

# 第二段：游标 API，首批游标中途过期
NEW_PAGES = {
    1: {"items": [{"id": 1}], "next_cursor": "new-c2"},
    "new-c2": {"items": [{"id": 3}], "next_cursor": None},
}
OLD_PAGES = {
    1: {"items": [{"id": 1}], "next_cursor": "old-c2"},
    "old-c2": {"items": [{"id": 2}], "next_cursor": "old-c3"},
    "old-c3": None,
}
expired = {"once": False}


def fetch(token):
    if not expired["once"]:
        if token == "old-c3":
            expired["once"] = True
            raise CursorExpired("游标已过期，回首页重新取")
        return OLD_PAGES[token]
    return NEW_PAGES[token]


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "checkpoint.json")
    result = walk(fetch)
    save_checkpoint(path, result["state"])
    print(
        "游标过期恢复: items=%s pages=%s expired=%s skipped=%s duplicate=%s"
        % (
            result["items"],
            result["pages"],
            result["expired"],
            result["skipped_pages"],
            result["duplicate"],
        )
    )
    print("断点落盘:", load_checkpoint(path))
