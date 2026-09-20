import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retry import backoff_delays, call_with_retries


class TestBackoff(unittest.TestCase):
    def test_plain_exponential(self):
        self.assertEqual(backoff_delays(4, base=0.5, factor=2.0), [0.5, 1.0, 2.0, 4.0])

    def test_single_attempt(self):
        self.assertEqual(backoff_delays(1), [0.5])

    def test_max_delay_caps_every_step(self):
        self.assertEqual(backoff_delays(5, base=1.0, factor=3.0, max_delay=5.0),
                         [1.0, 3.0, 5.0, 5.0, 5.0])

    def test_max_delay_reaches_the_caller(self):
        slept = []
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("nope")
            return "ok"

        result = call_with_retries(flaky, attempts=4, base=2.0, factor=4.0,
                                   max_delay=3.0, sleep=slept.append)
        self.assertEqual(result, "ok")
        self.assertEqual(slept, [2.0, 3.0])


if __name__ == "__main__":
    unittest.main()
