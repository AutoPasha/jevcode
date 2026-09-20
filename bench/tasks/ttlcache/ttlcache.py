"""A cache that forgets."""


class TTLCache:
    def __init__(self, ttl=60, clock=None):
        self.ttl = ttl
        self.clock = clock or (lambda: 0)
        self.items = {}

    def put(self, key, value):
        self.items[key] = (value, self.clock())

    def get(self, key, default=None):
        found = self.items.get(key)
        if found is None:
            return default
        value, stored = found
        if self.clock() - stored > self.ttl:
            del self.items[key]
            return default
        return value

    def __len__(self):
        return len(self.items)
