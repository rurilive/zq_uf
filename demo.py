"""演示：页码从 0 开始的接口 + 中断后断点恢复。"""

import os
import tempfile

from paginate import walk

ARTICLES = {0: ["文章一", "文章二"], 1: ["文章三", "文章四"], 2: ["文章五"]}


def fetch_page(page):
    """模拟页码从 0 开始的接口，响应里带页码元信息。"""
    return {
        "items": ARTICLES.get(page, []),
        "page": page,
        "first_page": 0,
        "total_pages": len(ARTICLES),
    }


def make_flaky_fetch():
    down = {"yes": True}

    def fetch(page):
        if page == 1 and down["yes"]:
            down["yes"] = False
            raise ValueError("接口故障")
        return fetch_page(page)

    return fetch


if __name__ == "__main__":
    print(walk(fetch_page))  # 首屏（第 0 页）不跳过

    state = os.path.join(tempfile.gettempdir(), "paginate-demo-state.json")
    flaky = make_flaky_fetch()
    try:
        walk(flaky, state_path=state)
    except ValueError:
        print("中断，进度已落盘:", state)
    print(walk(flaky, state_path=state))  # 从断点继续，不重抓已完成的页
