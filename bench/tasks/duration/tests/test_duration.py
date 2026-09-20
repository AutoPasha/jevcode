import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duration import human_duration


class TestDuration(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(human_duration(0), "0s")
        self.assertEqual(human_duration(45), "45s")

    def test_minutes_and_hours(self):
        self.assertEqual(human_duration(90), "1m 30s")
        self.assertEqual(human_duration(3700), "1h 1m 40s")

    def test_negative_is_refused(self):
        with self.assertRaises(ValueError):
            human_duration(-1)

    def test_days(self):
        self.assertEqual(human_duration(86400), "1d")
        self.assertEqual(human_duration(90061), "1d 1h 1m 1s")
        self.assertEqual(human_duration(172800), "2d")


if __name__ == "__main__":
    unittest.main()
