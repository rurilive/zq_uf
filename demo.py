"""游标分页站点演示：崩溃恢复、限速退避、追踪参数去重。"""
import os
import tempfile

from pagedigest import fetch_all, parse_page, strip_tracking_params

SITE = {
    None: {
        "items": [
            {"id": 1, "title": "机械键盘", "price": "299.00",
             "url": "http://shop/p/1?spm=abc.def"},
            {"id": 2, "title": "显示器", "price": "1299.00", "url": "http://shop/p/2"},
        ],
        "next_cursor": "c1",
    },
    "c1": {
        # 同一商品换了追踪参数并降价，应按指纹覆盖更新而不是新增
        "items": [{"id": 1, "title": "机械键盘", "price": "279.00",
                   "url": "http://shop/p/1?utm_source=push"}],
        "next_cursor": "c2",
    },
    "c2": {"items": []},  # 空页 = 正常结束
}


def make_fetch(crash_once_at=None):
    crashed = {"done": False}

    def fetch_page(cursor):
        if crash_once_at and cursor == crash_once_at and not crashed["done"]:
            crashed["done"] = True
            raise ConnectionError("模拟网络中断")
        return SITE[cursor]

    return fetch_page


def main():
    workdir = tempfile.mkdtemp(prefix="pagedigest-demo-")
    checkpoint = os.path.join(workdir, "checkpoint.json")
    invalid_path = os.path.join(workdir, "invalid.jsonl")
    print("工作目录:", workdir)

    print("\n[1] 首次抓取，在游标 c2 处模拟进程中断")
    try:
        fetch_all(
            make_fetch(crash_once_at="c2"),
            initial_cursor=None,
            checkpoint_path=checkpoint,
            invalid_sample_path=invalid_path,
            rate_interval=0.0,
        )
    except ConnectionError as exc:
        print("   中断:", exc)

    print("[2] 重启，从断点游标继续，直到空页结束")
    items = fetch_all(
        make_fetch(),
        initial_cursor=None,
        checkpoint_path=checkpoint,
        invalid_sample_path=invalid_path,
    )
    print("   抓到", len(items), "条（重复商品已覆盖）:")
    for item in items:
        print("  ", item.item_id, item.title, item.price, strip_tracking_params(item.url))

    print("\n[3] 再次运行，断点已标记 done，直接返回本地结果")
    again = fetch_all(make_fetch(), initial_cursor=None, checkpoint_path=checkpoint)
    print("   返回", len(again), "条，无网络请求")

    print("\n[4] 字段缺失样本只跳过不整批失败:")
    print("  ", parse_page({"items": [
        {"id": 9, "title": "鼠标", "price": "89.00", "url": "http://shop/p/9"},
        {"title": "缺 id 和 url"},
    ]}))


if __name__ == "__main__":
    main()
