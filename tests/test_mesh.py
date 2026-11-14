import unittest

from ratemesh import give_back, take
from ratemesh import mesh


class MeshTest(unittest.TestCase):
    def setUp(self):
        mesh._used.clear()

    def test_parent_counter_shared(self):
        take("p", "a", 2)
        take("p", "b", 3)
        self.assertEqual(mesh._used["p"], 5)

    def test_no_borrow_between_children(self):
        take("p", "a", 2)
        self.assertNotIn("a", mesh._used)

    def test_give_back_is_global(self):
        take("p", "a", 2)
        self.assertEqual(give_back("a", 1)["p"], 1)
