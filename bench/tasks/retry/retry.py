"""Retrying a flaky call with exponential backoff."""

import time


def backoff_delays(attempts, base=0.5, factor=2.0):
    """The delay before each retry, in seconds."""
    return [base * (factor ** n) for n in range(attempts)]


def call_with_retries(fn, attempts=4, base=0.5, factor=2.0, sleep=time.sleep):
    last = None
    for n, delay in enumerate(backoff_delays(attempts, base, factor)):
        try:
            return fn()
        except Exception as ex:          # noqa: BLE001 - retried and re-raised below
            last = ex
            if n < attempts - 1:
                sleep(delay)
    raise last
