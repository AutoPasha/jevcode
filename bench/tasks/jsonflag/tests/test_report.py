import io
import json
import unittest
from contextlib import redirect_stdout

from report import main


class ReportTest(unittest.TestCase):
    def run_it(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            main(argv)
        return out.getvalue()

    def test_text_is_unchanged(self):
        self.assertIn("count: 3", self.run_it(["1", "2", "3"]))

    def test_json_flag(self):
        payload = json.loads(self.run_it(["--json", "1", "2", "3"]))
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["total"], 6)


if __name__ == "__main__":
    unittest.main()
