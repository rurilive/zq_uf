"""演示：索引一批数据表，执行搜索，并验证序列化后结果可复现。"""
import json

from datasearch import SearchIndex, search

TABLE_BATCH = [
    {"name": "dwd_order_detail", "desc": "订单交易明细",
     "fields": ["order_id", "amount", "pay_time"], "tags": ["trade"], "popularity": 320},
    {"name": "dim_user", "desc": "用户维表",
     "fields": ["user_id", "city"], "tags": ["user"], "popularity": 180},
    {"name": "dws_sales_daily", "desc": "销售成交额汇总",
     "fields": ["order_id", "gmv"], "tags": ["sales"], "popularity": 260},
    {"name": "ods_payment", "desc": "支付流水",
     "fields": ["pay_id", "amount"], "tags": ["trade"], "popularity": 90},
]


def build_index():
    index = SearchIndex()
    for table in TABLE_BATCH:
        index.add_table(**table)
    return index


def show(index, query):
    hits = index.search_detail(query)
    print("%s -> %s" % (query, [hit["table"]["name"] for hit in hits]))
    for hit in hits:
        snippet = hit["snippets"][0]
        print("  %s score=%s hit=%s:%s" % (
            hit["table"]["name"], hit["score"], snippet["where"], snippet["text"]))


def main():
    print("== 默认索引 ==")
    for query in ("orders", "订单", "amount"):
        print("%s -> %s" % (query, [table["name"] for table in search(query)]))
    print("== 自建索引 ==")
    index = build_index()
    for query in ("订单", "amo", "成交额"):
        show(index, query)
    restored = SearchIndex.from_dict(json.loads(json.dumps(index.to_dict())))
    print("序列化复现 -> %s" % (restored.search("订单") == index.search("订单")))


if __name__ == "__main__":
    main()
