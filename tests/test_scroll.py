"""Tests for feedback-driven scrolling.

The fake page models what a real site does: after a scroll it shows skeleton
rows while ``loading`` is true / the network is busy, then re-renders the full
visible list. Items are deduplicated by identity, so a page that re-renders
overlapping rows (as virtual lists do) is handled too.
"""

import unittest

from scrollload import StopStats, done, scroll


class Clock:
    """Deterministic time source; ``sleep`` advances it."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Page:
    """A scripted scrolling page.

    ``views`` is the list of visible item sets, one per state: ``views[0]`` is
    the initial viewport, each scroll advances to the next one. ``delay`` is how
    long the page keeps ``loading`` true (and the network busy) after a scroll;
    ``busy_for`` keeps it busy from the very first read (a slow first load).
    """

    def __init__(self, views, clock=None, delay=0.2, busy_for=0.0, site="shop.test",
                 has_more=None, at_end=False, url=None):
        self.views = [list(view) for view in views]
        self.clock = clock or Clock()
        self.delay = delay
        self.site = site
        self.url = url

        self.index = 0
        self.scrolls = 0
        self.ready_at = self.clock() + busy_for
        self.reads = 0
        self.collect_calls = 0

        self._has_more = has_more
        self.at_end = at_end
        self.error = None
        self.snapshot_data = {"url": url or "https://shop.test/list", "html": "<div id=app>"}

    # -- page protocol -----------------------------------------------------
    @property
    def loading(self):
        return self.clock() < self.ready_at

    @property
    def network_idle(self):
        return self.clock() >= self.ready_at

    @property
    def has_more(self):
        if self._has_more is not None:
            return self._has_more
        return self.index < len(self.views) - 1

    @has_more.setter
    def has_more(self, value):
        self._has_more = value

    @property
    def items(self):
        self.reads += 1
        if self.loading:
            return [{"id": f"skeleton-{self.index}", "skeleton": True}]
        return self.views[min(self.index, len(self.views) - 1)]

    def collect(self):
        self.collect_calls += 1
        return self.items

    def scroll(self):
        self.scrolls += 1
        self.ready_at = self.clock() + self.delay
        if self.index < len(self.views) - 1:
            self.index += 1

    def snapshot(self):
        return self.snapshot_data


class ExplodingPage(Page):
    def __init__(self, *args, fail_on_scroll=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_on_scroll = fail_on_scroll

    def scroll(self):
        super().scroll()
        if self.scrolls == self.fail_on_scroll:
            raise RuntimeError("scroll: element is not attached to the document")


def long_views(pages=6, per_page=8):
    items = [{"id": f"item-{i}", "title": f"Product {i}"} for i in range(pages * per_page)]
    return [items[: (n + 1) * per_page] for n in range(pages)]


class ScrollTest(unittest.TestCase):
    def run_scroll(self, page, **kwargs):
        clock = page.clock
        kwargs.setdefault("clock", clock)
        kwargs.setdefault("sleep", clock.sleep)
        kwargs.setdefault("poll", 0.05)
        kwargs.setdefault("wait_timeout", 2.0)
        return scroll(page, **kwargs)

    # -- long list ---------------------------------------------------------
    def test_long_list_collects_everything_and_stops_on_no_more(self):
        page = Page(long_views())
        result = self.run_scroll(page)

        self.assertEqual(result["reason"], "done")
        self.assertEqual(result["added"], 48)
        self.assertEqual(result["seen"], 48)
        self.assertEqual(result["rounds"], 5)  # 6 views -> bootstrap + 5 scrolls
        self.assertTrue(result["done"])
        self.assertEqual([e["outcome"] for e in result["events"]][0], "bootstrap")

    def test_rounds_are_bounded_by_max_rounds(self):
        page = Page(long_views(pages=20, per_page=4), delay=0.1)
        result = self.run_scroll(page, max_rounds=3)
        self.assertEqual(result["rounds"], 3)
        self.assertEqual(result["reason"], "max_rounds")
        self.assertEqual(result["added"], 16)  # bootstrap + 3 scrolls, 4 per view
        self.assertEqual(page.scrolls, 3)

    # -- short list --------------------------------------------------------
    def test_short_list_stops_without_scrolling(self):
        page = Page([[{"id": "only-1"}, {"id": "only-2"}, {"id": "only-3"}]])
        result = self.run_scroll(page)

        self.assertEqual(result["rounds"], 0)
        self.assertEqual(result["added"], 3)
        self.assertEqual(result["reason"], "done")
        self.assertEqual(page.scrolls, 0)

    def test_short_list_without_end_signal_stops_after_idle_rounds(self):
        # No end marker and no has_more: the page just never grows.
        page = Page([[{"id": "only-1"}, {"id": "only-2"}]], delay=0, has_more=True)

        result = self.run_scroll(page, idle_rounds=2)

        self.assertEqual(result["reason"], "idle")
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(result["added"], 2)
        self.assertEqual(page.scrolls, 2)

    def test_idle_rounds_is_configurable(self):
        page = Page([[{"id": "a"}]], delay=0, has_more=True)
        result = self.run_scroll(page, idle_rounds=4, max_rounds=10)
        self.assertEqual(result["reason"], "idle")
        self.assertEqual(result["rounds"], 4)

    # -- skeleton / waiting ------------------------------------------------
    def test_skeletons_are_never_collected(self):
        page = Page(long_views(pages=4, per_page=5), delay=0.8)
        result = self.run_scroll(page, poll=0.05)

        ids = [item["id"] for item in result["items"]]
        self.assertFalse(any("skeleton" in item for item in ids), ids)
        self.assertEqual(result["skeletons"], 0)
        self.assertEqual(result["added"], 20)
        self.assertEqual(result["reason"], "done")

    def test_explicit_skeleton_predicate_filters_placeholders(self):
        views = [
            [{"id": "a"}, {"id": "b"}],
            [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"placeholder": True}],
        ]
        page = Page(views, delay=0)
        result = self.run_scroll(page, skeleton=lambda item: "placeholder" in item)
        self.assertEqual(result["added"], 3)
        self.assertEqual(result["skeletons"], 1)

    def test_waits_while_network_is_busy(self):
        page = Page([[{"id": "a"}], [{"id": "a"}, {"id": "b"}]], delay=1.0)
        calls = []
        original_collect = page.collect

        def tracking_collect():
            calls.append(page.clock())
            return original_collect()

        result = self.run_scroll(page, collect=tracking_collect)
        self.assertEqual(result["added"], 2)
        # the second read must happen after the page settled
        self.assertTrue(all(t >= 1.0 for t in calls[1:]), calls)

    def test_timeout_keeps_evidence_and_partial_items(self):
        views = [[{"id": "a"}], [{"id": "a"}, {"id": "b"}]]
        page = Page(views, busy_for=10.0)
        result = self.run_scroll(page, wait_timeout=0.5)

        self.assertEqual(result["reason"], "timeout")  # bootstrap never settles
        self.assertEqual(result["added"], 0)
        self.assertEqual(len(result["errors"]), 1)
        evidence = result["errors"][0]
        self.assertEqual(evidence["kind"], "timeout")
        self.assertIn("did not become stable", evidence["error"])
        self.assertEqual(evidence["snapshot"]["page"]["url"], "https://shop.test/list")
        self.assertEqual(evidence["site"], "shop.test")

    # -- dedupe ------------------------------------------------------------
    def test_duplicate_items_are_removed_by_identity(self):
        views = [
            [{"id": "a"}, {"id": "b"}],
            [{"id": "b"}, {"id": "c"}],
            [{"id": "c"}, {"id": "d"}],
        ]
        page = Page(views, delay=0)
        result = self.run_scroll(page)
        self.assertEqual([item["id"] for item in result["items"]], ["a", "b", "c", "d"])
        self.assertEqual(result["added"], 4)
        self.assertEqual(result["seen"], 4)

    def test_virtual_list_recycled_rows_are_deduped(self):
        all_items = [{"id": f"row-{i}", "name": f"Row {i}"} for i in range(12)]
        # A virtual list only renders a 4-row window and recycles identities.
        views = [all_items[i:i + 4] for i in range(0, 12, 2)]
        page = Page(views, delay=0)
        result = self.run_scroll(page)
        self.assertEqual(result["added"], 12)
        self.assertEqual(len({item["id"] for item in result["items"]}), 12)
        self.assertEqual(result["reason"], "done")

    def test_string_items_are_deduped_too(self):
        page = Page([["card-1", "card-2"], ["card-2", "card-3"]], delay=0)
        result = self.run_scroll(page)
        self.assertEqual(result["added"], 3)
        self.assertEqual(result["items"], ["card-1", "card-2", "card-3"])

    # -- failure / resume --------------------------------------------------
    def test_scroll_error_keeps_evidence_and_seen_items(self):
        page = ExplodingPage(long_views(pages=4, per_page=4), delay=0.1,
                             fail_on_scroll=2)
        result = self.run_scroll(page)

        self.assertEqual(result["reason"], "error")
        self.assertTrue(result["errors"])
        error = result["errors"][0]
        self.assertEqual(error["kind"], "exception")
        self.assertIn("not attached", error["error"])
        self.assertIn("Traceback", error["traceback"])
        self.assertEqual(error["round"], 2)
        self.assertGreater(result["seen"], 0)
        self.assertEqual(error["snapshot"]["page"]["html"], "<div id=app>")
        # evidence can be reviewed afterwards: it holds the seen tail
        self.assertEqual(len(error["snapshot"]["seen_tail"]), result["seen"])

    def test_resume_after_failure_continues_from_seen(self):
        page = ExplodingPage(long_views(pages=4, per_page=4), delay=0.1,
                             fail_on_scroll=1)
        failed = self.run_scroll(page)
        self.assertEqual(failed["reason"], "error")
        seen_before = failed["seen"]
        self.assertGreater(seen_before, 0)

        page.fail_on_scroll = 99
        page.delay = 0
        resumed = self.run_scroll(page, seen=failed)

        self.assertEqual(resumed["reason"], "done")
        # no item is counted twice across the two runs
        self.assertEqual(failed["added"] + resumed["added"], 16)
        self.assertEqual(resumed["seen"], 16)

    def test_resume_accepts_explicit_identity_list(self):
        page = Page(long_views(pages=3, per_page=4), delay=0)
        result = self.run_scroll(page, seen=["item-0", "item-1"])
        self.assertEqual(result["seen"], 12)
        self.assertEqual(result["added"], 10)

    def test_retries_recover_from_a_transient_scroll_error(self):
        page = ExplodingPage(long_views(pages=4, per_page=4), delay=0.1,
                             fail_on_scroll=1)
        result = self.run_scroll(page, retries=1)

        self.assertEqual(result["reason"], "done")
        self.assertEqual(result["added"], 16)
        self.assertEqual(result["errors"][0]["round"], 1)

    def test_page_reported_error_is_treated_as_failure(self):
        page = Page([[{"id": "a"}]], delay=0)
        page.error = RuntimeError("browser disconnected")
        result = self.run_scroll(page)
        self.assertEqual(result["reason"], "error")
        self.assertIn("browser disconnected", result["errors"][0]["error"])

    # -- statistics --------------------------------------------------------
    def test_stop_reasons_are_counted_per_site(self):
        stats = StopStats()
        long_page = Page(long_views(pages=2, per_page=2), delay=0, site="long.test")
        short_page = Page([[{"id": "x"}]], delay=0, site="short.test")

        self.run_scroll(long_page, stats=stats)
        self.run_scroll(short_page, stats=stats)

        self.assertEqual(stats.counts("long.test")["done"], 1)
        self.assertEqual(stats.counts("short.test")["done"], 1)
        self.assertEqual(stats.counts()["done"], 2)
        self.assertEqual(stats.report()["long.test"]["total"], 1)

    def test_site_defaults_to_url_host(self):
        page = Page([[{"id": "x"}]], delay=0, site=None,
                    url="https://www.example.com/list?page=2")
        result = self.run_scroll(page)
        self.assertEqual(result["site"], "www.example.com")

    # -- done() ------------------------------------------------------------
    def test_done_ignores_height_on_virtual_list(self):
        page = Page([[{"id": "a"}]], delay=0)
        page.has_more = True
        page.height = 1000  # unchanged, as virtual lists keep it
        self.assertFalse(done(page)["done"])
        self.assertEqual(done(page)["reason"], "has_more")

    def test_done_reports_end_marker(self):
        page = Page([[{"id": "a"}]], delay=0, at_end=True)
        self.assertEqual(done(page), {"done": True, "reason": "end_marker"})

    def test_done_reports_loading_and_busy(self):
        page = Page([[{"id": "a"}]], busy_for=1.0, has_more=True)
        self.assertEqual(done(page), {"done": False, "reason": "loading"})
        page.clock.now = 5.0
        self.assertEqual(done(page), {"done": False, "reason": "has_more"})

    def test_done_without_signals_is_not_done(self):
        self.assertFalse(done({})["done"])
        self.assertEqual(done({})["reason"], "no_signal")

    # -- validation --------------------------------------------------------
    def test_invalid_options_are_rejected(self):
        page = Page([[{"id": "a"}]], delay=0)
        for kwargs in (
            {"max_rounds": -1},
            {"idle_rounds": 0},
            {"poll": 0},
            {"wait_timeout": -1},
            {"retries": -1},
            {"max_rounds": "3"},
            {"poll": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                scroll(page, **kwargs)


if __name__ == "__main__":
    unittest.main()
