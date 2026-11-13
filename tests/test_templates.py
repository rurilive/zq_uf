import unittest

from detailcrawl import crawl

TEMPLATE_A = (
    '<html><head><title>{title}</title></head>'
    '<body><span data-field="price">{price}</span></body></html>'
)
TEMPLATE_B = (
    '<html><body><h1 data-field="name">{name}</h1>'
    '<i data-field="stock">{stock}</i></body></html>'
)


def make_pages():
    pages = {}
    for i in range(3):
        pages[f"http://a/{i}"] = TEMPLATE_A.format(title=f"t{i}", price=i)
    for i in range(2):
        pages[f"http://b/{i}"] = TEMPLATE_B.format(name=f"n{i}", stock=i)
    return pages


class TemplateGroupingTest(unittest.TestCase):
    def test_grouped_by_template_with_shared_rules(self):
        pages = make_pages()
        result = crawl(list(pages), pages.get, parse="template")
        self.assertEqual(len(result.stats.templates), 2)
        self.assertEqual(sorted(result.stats.templates.values()), [2, 3])
        self.assertEqual(result.stats.rules_built, 2)
        self.assertEqual(len(result.stats.succeeded), 5)

    def test_fields_extracted_per_page(self):
        pages = make_pages()
        result = crawl(list(pages), pages.get, parse="template")
        self.assertEqual(result[0], {"title": "t0", "price": "0"})
        self.assertEqual(result[2], {"title": "t2", "price": "2"})
        self.assertEqual(result[3], {"name": "n0", "stock": "0"})
        self.assertEqual(result[4], {"name": "n1", "stock": "1"})

    def test_parse_failure_recorded_without_retry(self):
        pages = {"http://x/1": "<html><body>no fields</body></html>"}
        result = crawl(["http://x/1"], pages.get, parse="template", retries=2)
        self.assertIsNone(result[0])
        self.assertEqual(len(result.stats.failed), 1)
        self.assertEqual(result.stats.failed[0].attempts, 1)
        self.assertIn("parse error", result.stats.failed[0].detail)

    def test_new_template_skeleton_gets_own_rules(self):
        pages = {
            "http://x/1": TEMPLATE_A.format(title="t", price=1),
            "http://x/2": (
                '<html><head><title>t2</title></head><body>'
                '<div><span data-field="price">2</span></div></body></html>'
            ),
        }
        result = crawl(list(pages), pages.get, parse="template")
        self.assertEqual(result.stats.rules_built, 2)
        self.assertEqual(len(result.stats.succeeded), 2)


if __name__ == "__main__":
    unittest.main()
