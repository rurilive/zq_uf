import os
import tempfile
import time
import unittest

from paginate import walk

PAGES = {1: ["a", "b"], 2: ["c"]}


class PaginateTest(unittest.TestCase):
    def test_collects_all_pages(self):
        self.assertEqual(walk(lambda page: PAGES.get(page, [])), ["a", "b", "c"])

    def test_stops_on_empty(self):
        calls = []

        def fetch(page):
            calls.append(page)
            return PAGES.get(page, [])

        walk(fetch)
        self.assertEqual(calls, [1, 2, 3])

    def test_empty_first_page(self):
        self.assertEqual(walk(lambda page: []), [])


class DualModeTest(unittest.TestCase):
    def test_page_mode_dict_response(self):
        def fetch(page):
            return {"items": ["p%s" % page], "page": page, "total_pages": 3}

        self.assertEqual(walk(fetch), ["p1", "p2", "p3"])

    def test_cursor_mode(self):
        table = {None: (["a", "b"], "c2"), "c2": (["c"], None)}

        def fetch(cursor):
            items, nxt = table.get(cursor, ([], None))
            return {"items": items, "next_cursor": nxt}

        self.assertEqual(walk(fetch, start=None), ["a", "b", "c"])

    def test_mode_switch_follows_response_fields(self):
        def fetch(token):
            if token == 1:
                return {"items": ["a"], "page": 1, "total_pages": 9, "next_cursor": "c2"}
            if token == "c2":
                return {"items": ["b"], "next_cursor": None}
            return {"items": []}

        self.assertEqual(walk(fetch), ["a", "b"])


class StartPageTest(unittest.TestCase):
    def test_zero_based_api_first_screen_not_skipped(self):
        data = {0: ["x"], 1: ["y"], 2: ["z"]}

        def fetch(page):
            return {"items": data.get(page, []), "page": page,
                    "first_page": 0, "total_pages": 3}

        self.assertEqual(walk(fetch), ["x", "y", "z"])


class TotalPagesTest(unittest.TestCase):
    def test_total_pages_grows_midway(self):
        totals = {1: 2, 2: 3, 3: 3}

        def fetch(page):
            return {"items": ["p%s" % page], "page": page,
                    "total_pages": totals.get(page, 3)}

        self.assertEqual(walk(fetch), ["p1", "p2", "p3"])

    def test_total_pages_shrinks_midway(self):
        totals = {1: 5, 2: 2}

        def fetch(page):
            return {"items": ["p%s" % page], "page": page,
                    "total_pages": totals.get(page, 2)}

        self.assertEqual(walk(fetch), ["p1", "p2"])


class DedupTest(unittest.TestCase):
    def test_overlap_dedup_and_duplicate_page_ends(self):
        data = {1: ["a", "b"], 2: ["b", "c"], 3: ["c", "d"], 4: ["c", "d"]}
        calls = []

        def fetch(page):
            calls.append(page)
            return data.get(page, [])

        self.assertEqual(walk(fetch), ["a", "b", "c", "d"])
        self.assertEqual(calls, [1, 2, 3, 4])

    def test_sliding_window_eviction(self):
        data = {1: ["a"], 2: ["b"], 3: ["a"]}

        def fetch(page):
            return data.get(page, [])

        self.assertEqual(walk(fetch, dedup_window=1), ["a", "b", "a"])


class RetryTest(unittest.TestCase):
    def test_transient_errors_retried_in_order(self):
        attempts = {"n": 0}
        calls = []

        def fetch(page):
            calls.append(page)
            if page == 2 and attempts["n"] < 2:
                attempts["n"] += 1
                raise ConnectionError("flaky")
            return PAGES.get(page, [])

        self.assertEqual(walk(fetch, backoff=0), ["a", "b", "c"])
        self.assertEqual(calls, [1, 2, 2, 2, 3])

    def test_non_transient_error_raises(self):
        def fetch(page):
            raise ValueError("bad")

        with self.assertRaises(ValueError):
            walk(fetch)


class ResumeTest(unittest.TestCase):
    def test_resume_from_saved_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state.json")
            broken = {"yes": True}
            calls = []

            def fetch(page):
                calls.append(page)
                if page == 2 and broken["yes"]:
                    raise ValueError("boom")
                return PAGES.get(page, [])

            with self.assertRaises(ValueError):
                walk(fetch, state_path=state)
            self.assertTrue(os.path.exists(state))

            broken["yes"] = False
            calls.clear()
            self.assertEqual(walk(fetch, state_path=state), ["a", "b", "c"])
            self.assertEqual(calls, [2, 3])
            self.assertFalse(os.path.exists(state))


class ConcurrencyTest(unittest.TestCase):
    def test_output_ordered_by_page_not_completion(self):
        delays = {1: 0.05, 2: 0.01, 3: 0}

        def fetch(page):
            time.sleep(delays.get(page, 0))
            return PAGES.get(page, [])

        self.assertEqual(walk(fetch, concurrency=3), ["a", "b", "c"])

    def test_concurrent_page_mode_respects_total(self):
        def fetch(page):
            return {"items": ["p%s" % page], "page": page, "total_pages": 2}

        self.assertEqual(walk(fetch, concurrency=4), ["p1", "p2"])
