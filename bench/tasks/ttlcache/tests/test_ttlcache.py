import unittest

from ttlcache import TTLCache


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class TTLTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cache = TTLCache(ttl=10, clock=self.clock)

    def test_fresh_entries_are_there(self):
        self.cache.put("a", 1)
        self.clock.now = 9
        self.assertEqual(self.cache.get("a"), 1)

    def test_gone_exactly_at_the_ttl(self):
        self.cache.put("a", 1)
        self.clock.now = 10
        self.assertIsNone(self.cache.get("a"))

    def test_gone_later_too(self):
        self.cache.put("a", 1)
        self.clock.now = 99
        self.assertIsNone(self.cache.get("a"))


if __name__ == "__main__":
    unittest.main()
