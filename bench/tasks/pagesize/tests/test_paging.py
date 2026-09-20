import unittest

from service import list_page


ROWS = list(range(1, 26))


class PagingTest(unittest.TestCase):
    def test_default_page_is_still_ten(self):
        self.assertEqual(list_page(ROWS, 1)["rows"], list(range(1, 11)))

    def test_a_smaller_page(self):
        out = list_page(ROWS, 2, per_page=5)
        self.assertEqual(out["rows"], [6, 7, 8, 9, 10])

    def test_the_page_count_follows(self):
        self.assertEqual(list_page(ROWS, 1, per_page=5)["pages"], 5)


if __name__ == "__main__":
    unittest.main()
