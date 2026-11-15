import unittest

from snapalign import AlignError, ChangeStream, apply, repair, snapshot, write


def store(*tables):
    """构造多表库: store(("orders", rows), ...)。"""
    return {name: {"rows": rows} for name, rows in tables}


def evt(op, row, table="orders"):
    return {"op": op, "table": table, "row": row}


class SnapshotPositionTest(unittest.TestCase):
    def test_snapshot_records_start_position(self):
        stream = ChangeStream()
        write(store(("orders", [])), stream, evt("upsert", {"id": 1}))
        write(store(("orders", [])), stream, evt("upsert", {"id": 2}))
        session = snapshot(store(("orders", [{"id": 1}, {"id": 2}])), stream)
        # 快照开始位点 = 流当前头，之前的事件不重放
        self.assertEqual(session.start_position, 2)
        self.assertEqual(session.position, 2)
        self.assertEqual(session.cached, 0)

    def test_changes_during_snapshot_are_cached_not_lost(self):
        stream = ChangeStream()
        src = store(("orders", [{"id": 1}]))
        session = snapshot(src, stream)
        # 快照进行中，生产库持续产生变更
        write(src, stream, evt("upsert", {"id": 2}))
        write(src, stream, evt("upsert", {"id": 3}))
        write(src, stream, evt("delete", {"id": 1}))
        self.assertEqual(session.cached, 3)
        self.assertEqual([e["position"] for e in session.cached_events()], [0, 1, 2])

        result = session.resume()
        self.assertTrue(result["ok"])
        self.assertEqual(result["position"], 3)
        self.assertEqual(result["cached"], 0)
        self.assertEqual(result["applied"], 3)
        # 快照期间的变更一条没丢，删除也生效
        self.assertEqual(
            sorted(r["id"] for r in session.tables["orders"]["rows"]), [2, 3]
        )

    def test_events_apply_in_position_order(self):
        stream = ChangeStream()
        src = store(("orders", []))
        session = snapshot(src, stream)
        write(src, stream, evt("upsert", {"id": 1, "v": "a"}))
        write(src, stream, evt("upsert", {"id": 1, "v": "b"}))
        session.resume()
        # 后一个位点覆盖前一个
        self.assertEqual(session.tables["orders"]["rows"], [{"id": 1, "v": "b"}])


class IdempotencyTest(unittest.TestCase):
    def test_reapply_is_idempotent_upsert_and_delete(self):
        stream = ChangeStream()
        src = store(("orders", [{"id": 1}]))
        session = snapshot(src, stream)
        write(src, stream, evt("upsert", {"id": 2}))
        write(src, stream, evt("delete", {"id": 1}))
        write(src, stream, evt("delete", {"id": 99}))  # 删除不存在的行：无操作
        session.resume()
        rows_after = [dict(r) for r in session.tables["orders"]["rows"]]

        # 已经追平，再 resume 一次：位点只向前，无新事件，结果不变
        result = session.resume()
        self.assertEqual(result["applied"], 0)
        self.assertEqual(session.tables["orders"]["rows"], rows_after)
        self.assertEqual(rows_after, [{"id": 2}])

    def test_position_only_moves_forward(self):
        stream = ChangeStream()
        src = store(("orders", []))
        session = snapshot(src, stream)
        for i in range(5):
            write(src, stream, evt("upsert", {"id": i}))
        session.resume()
        self.assertEqual(session.position, 5)
        # 日志头不变，位点不会回退
        session.resume()
        self.assertEqual(session.position, 5)


class FailureAndResumeTest(unittest.TestCase):
    def test_failure_stops_at_position_and_resume_continues(self):
        stream = ChangeStream()
        src = store(("orders", [{"id": 1}]))
        session = snapshot(src, stream)
        write(src, stream, evt("upsert", {"id": 2}))
        bad_position = write(
            src, stream, {"op": "corrupt", "table": "orders", "row": {"id": 3}}
        )
        write(src, stream, evt("upsert", {"id": 4}))  # 坏事件之后的变更

        with self.assertRaises(AlignError) as ctx:
            session.resume()
        self.assertEqual(ctx.exception.position, bad_position)
        # 停在原位点：id=2 已应用，坏事件和后续事件仍在缓存
        self.assertEqual(session.position, bad_position)
        self.assertEqual(session.cached, 2)
        self.assertEqual(
            sorted(r["id"] for r in session.tables["orders"]["rows"]), [1, 2]
        )

        # 原位点修复，恢复继续，后续变更接着应用
        repair(src, stream, bad_position, evt("upsert", {"id": 3}))
        result = session.resume()
        self.assertTrue(result["ok"])
        self.assertEqual(result["position"], 3)
        self.assertEqual(result["applied"], 2)
        self.assertEqual(
            sorted(r["id"] for r in session.tables["orders"]["rows"]),
            [1, 2, 3, 4],
        )

    def test_unrepaired_event_stops_at_same_position_again(self):
        stream = ChangeStream()
        src = store(("orders", []))
        session = snapshot(src, stream)
        bad = write(src, stream, {"op": "?", "row": {"id": 1}})
        for _ in range(2):
            with self.assertRaises(AlignError):
                session.resume()
            self.assertEqual(session.position, bad)  # 一直停在原位点

    def test_missing_primary_key_is_failure_too(self):
        stream = ChangeStream()
        src = store(("orders", []))
        session = snapshot(src, stream)
        write(src, stream, {"op": "upsert", "row": {"no_id": True}})
        with self.assertRaises(AlignError):
            session.resume()
        self.assertEqual(session.position, 0)

    def test_bad_event_leaves_no_ghost_table(self):
        stream = ChangeStream()
        src = store(("orders", []))
        session = snapshot(src, stream)
        write(src, stream, {"op": "bad-write", "row": {"id": 4}})
        with self.assertRaises(AlignError):
            session.resume()
        # 坏事件没有合法 table，不得凭空建出 default 空表
        self.assertEqual(set(session.tables), {"orders"})


class VerifyTest(unittest.TestCase):
    def test_verify_row_count_and_content_per_table(self):
        stream = ChangeStream()
        src = store(
            ("orders", [{"id": 1}, {"id": 2}]),
            ("items", [{"id": 9}]),
        )
        session = snapshot(src, stream)
        write(src, stream, evt("upsert", {"id": 3}, table="orders"))
        write(src, stream, evt("upsert", {"id": 8}, table="items"))
        session.resume()

        result = session.verify()
        self.assertTrue(result["ok"])
        self.assertTrue(result["caught_up"])
        self.assertEqual(result["diffs"], [])

        # 篡改副本：orders 行数差异，items 内容差异，差异按表分别给出
        session.tables["orders"]["rows"].pop()
        session.tables["items"]["rows"][0]["v"] = "tampered"
        diffs = session.verify()["diffs"]
        self.assertEqual([d["table"] for d in diffs], ["items", "orders"])
        kinds = {d["table"]: d["kind"] for d in diffs}
        self.assertEqual(kinds["orders"], "row_count")
        self.assertEqual(kinds["items"], "content")

    def test_verify_not_caught_up_reports_diffs_against_source(self):
        stream = ChangeStream()
        src = store(("orders", []))
        session = snapshot(src, stream)
        write(src, stream, evt("upsert", {"id": 1}))
        # 不 resume：尚未追平，校验应显示与生产库的差异
        result = session.verify()
        self.assertFalse(result["ok"])
        self.assertFalse(result["caught_up"])
        self.assertEqual(result["diffs"][0]["kind"], "row_count")


class EndToEndRecoveryDrillTest(unittest.TestCase):
    """需求验收：先在测试库演练一次恢复，确认快照期间的变更没有丢。"""

    def test_full_recovery_drill(self):
        stream = ChangeStream()
        src = store(
            ("orders", [{"id": 1}, {"id": 2}]),
            ("items", [{"id": 100}]),
        )

        # 1. 快照开始，记录位点 0
        session = snapshot(src, stream)
        self.assertEqual(session.start_position, 0)

        # 2. 快照期间生产库持续写入（含更新、删除、跨表）
        write(src, stream, evt("upsert", {"id": 1, "status": "paid"}))
        write(src, stream, evt("upsert", {"id": 3}))
        write(src, stream, evt("delete", {"id": 2}))
        write(src, stream, evt("upsert", {"id": 101}, table="items"))
        bad = write(
            src, stream, {"op": "bad-write", "row": {"id": 4}},
        )
        write(src, stream, evt("upsert", {"id": 5}))

        # 3. 首次恢复：应用到坏事件停下，位点不前进
        with self.assertRaises(AlignError):
            session.resume()
        self.assertEqual(session.position, bad)

        # 4. 原位点修复后继续，全部追平
        repair(src, stream, bad, evt("upsert", {"id": 4}))
        result = session.resume()
        self.assertTrue(result["ok"])
        self.assertEqual(result["cached"], 0)
        self.assertEqual(result["diffs"], [])

        # 5. 逐表行数 + 内容与生产库完全一致（行序无语义，按 id 排序比较）
        by_id = lambda rows: sorted(rows, key=lambda r: r["id"])
        self.assertEqual(
            sorted(r["id"] for r in session.tables["orders"]["rows"]), [1, 3, 4, 5]
        )
        self.assertEqual(
            by_id(session.tables["orders"]["rows"]), by_id(src["orders"]["rows"])
        )
        self.assertEqual(
            by_id(session.tables["items"]["rows"]), by_id(src["items"]["rows"])
        )
        # 更新后的内容也一致
        self.assertEqual(
            next(r for r in session.tables["orders"]["rows"] if r["id"] == 1),
            {"id": 1, "status": "paid"},
        )


class CompatShimTest(unittest.TestCase):
    def test_bare_table_snapshot_and_apply_shim(self):
        result = apply([evt("upsert", {"id": 1}, table="default")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["position"], 1)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["diffs"], [])

    def test_result_contains_required_fields(self):
        result = apply([])
        for field in (
            "position",
            "start_position",
            "cached",
            "applied",
            "skipped",
            "ok",
            "error",
            "caught_up",
            "diffs",
        ):
            self.assertIn(field, result)


if __name__ == "__main__":
    unittest.main()
