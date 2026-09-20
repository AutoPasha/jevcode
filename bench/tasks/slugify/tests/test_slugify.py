import unittest

from slugify import slugify


class SlugifyTest(unittest.TestCase):
    def test_lowercases_and_dashes(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_collapses_punctuation(self):
        self.assertEqual(slugify("Well, hello --- there!"), "well-hello-there")

    def test_trims_the_edges(self):
        self.assertEqual(slugify("  spaced out  "), "spaced-out")


if __name__ == "__main__":
    unittest.main()
