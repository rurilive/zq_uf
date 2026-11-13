from pagecycle import walk

PAGES = {
    "p1": {"url": "p1", "next": "p2"},
    "p2": {"url": "p2", "next": "p1"},
}

print(walk(lambda u: PAGES[u], start="p1", limit=6))

# 第二段：环状分页。游标页的 next 带排序/会话参数绕回第 1 页，
# 归一化判环后立即停止；state_path 落盘，resume 沿用已访问集合。
import os
import tempfile

SHOP = {
    "http://shop/list?page=1": {"url": "http://shop/list?page=1",
                                "next": "http://shop/list?page=2"},
    "http://shop/list?page=2": {"url": "http://shop/list?page=2",
                                "next": "http://shop/list?cursor=abc"},
    "http://shop/list?cursor=abc": {"url": "http://shop/list?cursor=abc",
                                    "next": "http://shop/list?page=1&sort=price&sid=x1"},
}

state_path = os.path.join(tempfile.gettempdir(), "pagecycle-demo-state.json")
if os.path.exists(state_path):
    os.remove(state_path)

first = walk(lambda u: SHOP[u], start="http://shop/list?page=1", limit=2,
             stop_on_cycle=True, state_path=state_path)
print("第一段(到limit):", list(first), "->", first.stop_reason)

resumed = walk(lambda u: SHOP[u], start="http://shop/list?page=1", limit=10,
               stop_on_cycle=True, state_path=state_path, resume=True)
print("断点续抓:", list(resumed), "->", resumed.stop_reason,
      "重复:", resumed.repeated)
