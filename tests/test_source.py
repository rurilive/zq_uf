import json
import os
import tempfile
import unittest
from urllib.error import HTTPError

from pagedigest import fetch_all, item_fingerprint, parse_page, strip_tracking_params

PAGES = {
    1: {"items": [{"id": 1, "title": "alpha", "price": "10.00", "url": "http://shop/1"}]},
    2: {"items": [{"id": 2, "title": "beta", "price": "20.00", "url": "http://shop/2"}]},
}


def fake_fetch(page):
    return PAGES.get(page, {"items": []})


class PagedigestTest(unittest.TestCase):
    def test_parse_fields(self):
        items = parse_page({"items": [{"id": 7, "title": "x", "price": "1.50", "url": "http://shop/7"}]})
        self.assertEqual(items[0].item_id, "7")
        self.assertEqual(items[0].price, "1.50")

    def test_fetch_two_pages(self):
        items = fetch_all(fake_fetch)
        self.assertEqual([item.title for item in items], ["alpha", "beta"])

    def test_empty_page_stops(self):
        calls = []

        def counting(page):
            calls.append(page)
            return PAGES.get(page, {"items": []})

        fetch_all(counting)
        self.assertEqual(calls, [1, 2, 3])


CURSOR_SITE = {
    None: {
        "items": [{"id": 1, "title": "alpha", "price": "10.00", "url": "http://shop/1"}],
        "next_cursor": "c1",
    },
    "c1": {
        "items": [{"id": 2, "title": "beta", "price": "20.00", "url": "http://shop/2"}],
        "next_cursor": "c2",
    },
    "c2": {"items": []},
}


class CursorFetchTest(unittest.TestCase):
    def test_cursor_pagination_uses_response_cursor(self):
        calls = []

        def fetch_page(cursor):
            calls.append(cursor)
            return CURSOR_SITE[cursor]

        items = fetch_all(fetch_page, initial_cursor=None)
        self.assertEqual(calls, [None, "c1", "c2"])
        self.assertEqual([item.item_id for item in items], ["1", "2"])

    def test_empty_array_ends_without_error(self):
        items = fetch_all(lambda cursor: CURSOR_SITE[cursor], initial_cursor=None)
        self.assertEqual(len(items), 2)

    def test_checkpoint_resume_after_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = os.path.join(tmp, "state.json")

            def flaky(cursor):
                if cursor == "c2":
                    raise RuntimeError("connection reset")
                return CURSOR_SITE[cursor]

            with self.assertRaises(RuntimeError):
                fetch_all(flaky, initial_cursor=None, checkpoint_path=checkpoint)

            with open(checkpoint, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertFalse(saved["done"])
            self.assertEqual(saved["cursor"], "c2")
            self.assertEqual(len(saved["items"]), 2)

            calls = []

            def resumed(cursor):
                calls.append(cursor)
                return CURSOR_SITE[cursor]

            items = fetch_all(resumed, initial_cursor=None, checkpoint_path=checkpoint)
            self.assertEqual(calls[0], "c2")
            self.assertEqual([item.item_id for item in items], ["1", "2"])
            with open(checkpoint, encoding="utf-8") as handle:
                self.assertTrue(json.load(handle)["done"])

    def test_done_checkpoint_short_circuits(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = os.path.join(tmp, "state.json")
            fetch_all(lambda cursor: CURSOR_SITE[cursor], initial_cursor=None,
                      checkpoint_path=checkpoint)

            def must_not_call(cursor):
                raise AssertionError("completed checkpoint should not hit network")

            items = fetch_all(must_not_call, initial_cursor=None, checkpoint_path=checkpoint)
            self.assertEqual([item.item_id for item in items], ["1", "2"])

    def test_rate_interval_between_requests(self):
        sleeps = []
        fetch_all(
            lambda cursor: CURSOR_SITE[cursor],
            initial_cursor=None,
            rate_interval=0.5,
            sleep=sleeps.append,
        )
        self.assertEqual(sleeps, [0.5, 0.5])

    def test_429_backoff_then_retry(self):
        sleeps = []
        attempts = {"n": 0}

        def flaky(cursor):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise HTTPError("http://shop/api", 429, "Too Many Requests", {}, None)
            return {"items": []}

        items = fetch_all(
            flaky,
            initial_cursor=None,
            backoff_start=1.0,
            backoff_factor=2.0,
            backoff_max=60.0,
            sleep=sleeps.append,
        )
        self.assertEqual(items, [])
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_429_retry_after_header(self):
        sleeps = []

        class RateLimited(Exception):
            code = 429
            headers = {"Retry-After": "3"}

        def flaky(cursor):
            raise RateLimited()

        with self.assertRaises(RateLimited):
            fetch_all(
                flaky,
                initial_cursor=None,
                max_retries=1,
                backoff_start=1.0,
                sleep=sleeps.append,
            )
        self.assertEqual(sleeps, [3.0])

    def test_429_exhausted_raises(self):
        def always_429(cursor):
            raise HTTPError("http://shop/api", 429, "Too Many Requests", {}, None)

        with self.assertRaises(HTTPError):
            fetch_all(always_429, initial_cursor=None, max_retries=2, sleep=lambda _: None)

    def test_duplicate_product_upserts_with_fingerprint(self):
        site = {
            None: {
                "items": [{
                    "id": 1,
                    "title": "键盘",
                    "price": "299.00",
                    "url": "http://shop/p/1?spm=a.b.c",
                }],
                "next_cursor": "c1",
            },
            "c1": {
                "items": [{
                    "id": 1,
                    "title": "键盘",
                    "price": "279.00",
                    "url": "http://shop/p/1?utm_source=push",
                }],
                "next_cursor": "c2",
            },
            "c2": {"items": []},
        }
        items = fetch_all(lambda cursor: site[cursor], initial_cursor=None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, "279.00")
        self.assertEqual(
            item_fingerprint("1", "http://shop/p/1?spm=a"),
            item_fingerprint("1", "http://shop/p/1?utm_source=x"),
        )
        self.assertEqual(
            strip_tracking_params("http://shop/p/1?spm=a&id=2"),
            "http://shop/p/1?id=2",
        )

    def test_missing_fields_are_sampled_and_skipped(self):
        samples = []
        payload = {
            "items": [
                {"id": 1, "title": "ok", "price": "1.00", "url": "http://shop/1"},
                {"id": 2, "title": "no-url"},
                {"title": "no-id", "url": "http://shop/3"},
                "not-a-dict",
            ]
        }
        items = parse_page(payload, on_invalid=samples.append)
        self.assertEqual([item.item_id for item in items], ["1"])
        self.assertEqual(len(samples), 3)

        with tempfile.TemporaryDirectory() as tmp:
            sample_path = os.path.join(tmp, "invalid.jsonl")
            payload_with_cursor = dict(payload, next_cursor="c1")
            site = {None: payload_with_cursor, "c1": {"items": []}}
            items = fetch_all(
                lambda cursor: site[cursor],
                initial_cursor=None,
                invalid_sample_path=sample_path,
            )
            self.assertEqual(len(items), 1)
            with open(sample_path, encoding="utf-8") as handle:
                lines = handle.read().strip().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertTrue(all(json.loads(line)["reason"] for line in lines))

    def test_all_invalid_page_with_next_cursor_continues(self):
        site = {
            None: {
                "items": [{"title": "no id/url"}],
                "next_cursor": "c1",
            },
            "c1": {
                "items": [{"id": 5, "title": "ok", "price": "1.00", "url": "http://shop/5"}],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            sample_path = os.path.join(tmp, "invalid.jsonl")
            items = fetch_all(
                lambda cursor: site[cursor],
                initial_cursor=None,
                invalid_sample_path=sample_path,
            )
            self.assertEqual([item.item_id for item in items], ["5"])
            with open(sample_path, encoding="utf-8") as handle:
                self.assertEqual(len(handle.read().strip().splitlines()), 1)

    def test_opaque_cursor_without_next_cursor_ends_no_duplicate(self):
        calls = []
        site = {
            "c1": {"items": [{"id": 1, "title": "a", "price": "1", "url": "http://s/1"}]},
        }

        def fetch_page(cursor):
            calls.append(cursor)
            return site[cursor]

        items = fetch_all(fetch_page, initial_cursor="c1")
        self.assertEqual(calls, ["c1"])
        self.assertEqual(len(items), 1)
