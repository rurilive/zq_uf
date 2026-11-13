"""数据目录搜索，当前只匹配表名。"""

TABLES = [
    {"name": "dwd_orders", "desc": "订单明细", "fields": ["order_id", "amount"]},
    {"name": "dim_user", "desc": "用户维表", "fields": ["user_id", "city"]},
    {"name": "dws_sales", "desc": "销售汇总", "fields": ["order_id", "gmv"]},
]


def search(query):
    """子串匹配表名，区分大小写，不排序。"""
    return [table for table in TABLES if query in table["name"]]
