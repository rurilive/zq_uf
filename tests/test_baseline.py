import unittest

from selectorwatch import check, reset, set_selectors


class BaselineTest(unittest.TestCase):
    def setUp(self):
        reset()

    def learn(self, site="shop"):
        return check(site, [{"title": "键盘", "price": "299"},
                            {"title": "鼠标", "price": "99"}])

    def test_baseline_learning_no_alert(self):
        result = self.learn()
        self.assertEqual(result["alerts"], [])
        result = check("shop", [{"title": "耳机", "price": "199"}])
        self.assertEqual(result["alerts"], [])

    def test_hit_rate_drop_alert(self):
        self.learn()
        result = check("shop", [{"title": "", "price": ""}] * 3)
        self.assertEqual(len(result["alerts"]), 2)
        alert = [a for a in result["alerts"] if a["field"] == "title"][0]
        self.assertEqual(alert["site"], "shop")
        self.assertEqual(alert["hit_rate"], 0.0)
        self.assertEqual(alert["baseline"], 1.0)
        self.assertEqual(alert["samples"], [0, 1, 2])
        self.assertIn("hit_rate", alert["reasons"])

    def test_structure_mutation(self):
        self.learn()
        result = check("shop", [{"title": "键盘", "price": "售价 99 元"}])
        alerts = [a for a in result["alerts"] if a["field"] == "price"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("structure", alerts[0]["reasons"])
        self.assertEqual(alerts[0]["level"], "alert")

    def test_consecutive_escalation(self):
        self.learn()
        result = check("shop", [{"title": "键盘", "price": "99"},
                                {"title": "", "price": ""},
                                {"title": "", "price": ""}])
        levels = {a["field"]: a["level"] for a in result["alerts"]}
        self.assertEqual(levels["title"], "escalated")
        self.assertEqual(levels["price"], "escalated")

    def test_silence_and_merge(self):
        self.learn()
        bad = [{"title": "", "price": ""}] * 3
        first = check("shop", bad)
        self.assertEqual(len(first["alerts"]), 2)
        self.assertEqual(check("shop", bad)["alerts"], [])
        self.assertEqual(check("shop", bad)["alerts"], [])
        self.assertEqual(first["alerts"][0]["repeats"], 3)
        fourth = check("shop", bad)
        self.assertEqual(len(fourth["alerts"]), 2)
        self.assertEqual(fourth["alerts"][0]["repeats"], 4)

    def test_samples_locate_pages(self):
        self.learn()
        result = check("shop", [{"title": "键盘", "price": "99"},
                                {"title": "鼠标", "price": "售价 9 元"},
                                {"title": "音箱", "price": "199"},
                                {"title": "耳机", "price": "售价 29 元"}])
        alert = [a for a in result["alerts"] if a["field"] == "price"][0]
        self.assertEqual(alert["samples"], [1, 3])

    def test_shared_selectors(self):
        set_selectors({"price": "span.price"})
        check("news", [{"price": "9"}])
        result = check("news", [{"price": ""}] * 3)
        self.assertEqual(result["alerts"][0]["selector"], "span.price")


if __name__ == "__main__":
    unittest.main()
