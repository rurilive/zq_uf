import unittest

from rowlineage import LineageError, propagate, record, reset, trace


class RowLineageTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
                "keys": {"raw.orders.id": "dwd.orders.order_id"},
                "rows": [("raw.orders", 1, "dwd.orders", 1),
                         {"src_table": "raw.orders", "src_key": 2,
                          "dst_table": "dwd.orders", "dst_key": 2}],
                "ts": 100})
        record({"job": "j2", "inputs": ["dwd.orders"], "outputs": ["dws.daily"],
                "keys": {"dwd.orders.order_id": "dws.daily.order_id"},
                "rows": [("dwd.orders", 1, "dws.daily", 1)],
                "ts": 200})

    # 主键映射：行级上下游与层级
    def test_key_mapping_levels(self):
        r = trace("dws.daily", 1)
        up = {(n["table"], n["key"]): n["level"] for n in r["upstream"]}
        self.assertEqual(up[("dwd.orders", 1)], 1)
        self.assertEqual(up[("raw.orders", 1)], 2)
        self.assertEqual(r["downstream"], [])

    # 路径：两跳链路完整路径
    def test_paths(self):
        r = trace("raw.orders", 1)
        paths = [[(n["table"], n["key"]) for n in p]
                 for p in r["paths"]["downstream"]]
        self.assertIn([("raw.orders", 1), ("dwd.orders", 1), ("dws.daily", 1)],
                      paths)

    # 增量传播：按边推进，层级递增
    def test_incremental_propagate(self):
        r = propagate("raw.orders", 1, update_id="u1")
        got = [(n["table"], n["key"], n["level"]) for n in r["updated"]]
        self.assertEqual(got, [("dwd.orders", 1, 1), ("dws.daily", 1, 2)])
        self.assertFalse(r["idempotent"])
        self.assertIn({"table": "raw.orders", "key": 1}, r["visited"])

    # 幂等：同一 update_id 重复传播为空操作
    def test_idempotent_propagate(self):
        first = propagate("raw.orders", 1, update_id="u1")
        again = propagate("raw.orders", 1, update_id="u1")
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["updated"], [])
        self.assertEqual(again["visited"], first["visited"])

    # 环路：检测并截断，保留已访问集合
    def test_cycle_truncated(self):
        record({"job": "j3", "inputs": ["dws.daily"], "outputs": ["raw.orders"],
                "rows": [("dws.daily", 1, "raw.orders", 1)], "ts": 300})
        r = trace("raw.orders", 1)
        self.assertIn({"table": "raw.orders", "key": 1}, r["truncated"])
        down = [(n["table"], n["key"]) for n in r["downstream"]]
        self.assertEqual(down, [("dwd.orders", 1), ("dws.daily", 1)])
        p = propagate("raw.orders", 1, update_id="u2")
        self.assertIn({"table": "raw.orders", "key": 1}, p["truncated"])
        visited = {(n["table"], n["key"]) for n in p["visited"]}
        self.assertEqual(visited,
                         {("raw.orders", 1), ("dwd.orders", 1), ("dws.daily", 1)})

    # SQL 解析：主键映射 + IN 过滤生成行级边
    def test_sql_in_filter(self):
        reset()
        record({"job": "j9",
                "sql": "INSERT INTO dwd.orders (order_id, amount) "
                       "SELECT id, amount FROM raw.orders WHERE id IN (1, 2)"})
        r = trace("raw.orders", 2)
        self.assertEqual([(n["table"], n["key"]) for n in r["downstream"]],
                         [("dwd.orders", 2)])
        r3 = trace("raw.orders", 3)  # 不在过滤值内，退化为表级
        self.assertIsInstance(r3, list)

    # SQL 解析：等值过滤 + 字符串主键
    def test_sql_eq_filter(self):
        reset()
        record({"job": "j10",
                "sql": "INSERT INTO dwd.users (uid) SELECT id FROM raw.users "
                       "WHERE id = 'u7'"})
        r = trace("raw.users", "u7")
        self.assertEqual([(n["table"], n["key"]) for n in r["downstream"]],
                         [("dwd.users", "u7")])

    # 查询过滤：按表和时间范围
    def test_table_and_time_filter(self):
        r = trace("raw.orders", 1, end=150)
        self.assertEqual([(n["table"], n["key"]) for n in r["downstream"]],
                         [("dwd.orders", 1)])
        r2 = trace("raw.orders", 1, tables={"raw.orders", "dwd.orders"})
        self.assertEqual([(n["table"], n["key"]) for n in r2["downstream"]],
                         [("dwd.orders", 1)])
        jobs = trace("dwd.orders", start=150)
        self.assertEqual([j["job"] for j in jobs], ["j2"])

    # 异常路径：明确错误
    def test_errors(self):
        with self.assertRaises(LineageError):
            record({"job": "x"})  # 缺 inputs/outputs
        with self.assertRaises(LineageError):
            record("not-a-dict")
        with self.assertRaises(LineageError):
            record({"inputs": ["a"], "outputs": ["b"],
                    "keys": {"a": "b.c"}})  # 列引用格式错
        with self.assertRaises(LineageError):
            record({"inputs": ["a"], "outputs": ["b"],
                    "rows": [("a", 1, "b")]})  # 行边缺字段
        with self.assertRaises(LineageError):
            record({"inputs": ["a"], "outputs": ["b"], "sql": "SELECT 1"})
        with self.assertRaises(LineageError):
            record({"inputs": ["a"], "outputs": ["b"],
                    "sql": "INSERT INTO t (x) SELECT a FROM s WHERE id = 1+"})
        with self.assertRaises(LineageError):
            trace("no.such.table")
        with self.assertRaises(LineageError):
            trace("raw.orders", object())
        with self.assertRaises(LineageError):
            trace("raw.orders", 1, start=200, end=100)


if __name__ == "__main__":
    unittest.main()
