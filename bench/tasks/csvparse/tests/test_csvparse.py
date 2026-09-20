import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csvparse import parse_line


class TestParse(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_line("a,b,c"), ["a", "b", "c"])

    def test_quoted_comma(self):
        self.assertEqual(parse_line('a,"b,c",d'), ["a", "b,c", "d"])

    def test_empty_fields(self):
        self.assertEqual(parse_line("a,,b"), ["a", "", "b"])

    def test_escaped_quote_inside_a_quoted_field(self):
        self.assertEqual(parse_line('a,"say ""hi""",b'), ["a", 'say "hi"', "b"])

    def test_escaped_quote_alone(self):
        self.assertEqual(parse_line('"""quoted"""'), ['"quoted"'])


if __name__ == "__main__":
    unittest.main()
