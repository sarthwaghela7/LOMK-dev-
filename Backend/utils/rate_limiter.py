"""Thread-safe token-bucket rate limiter shared across concurrent F3 workers.

One instance is shared by every thread in a process so total OpenAI requests
per minute across ALL concurrent workers stays under the account's RPM tier,
regardless of how many threads are running.
"""
import threading
import time


class TokenBucketRateLimiter:
    def __init__(self, max_per_minute):
        self.configured_capacity = max(1, max_per_minute)
        self.capacity = float(self.configured_capacity)
        self.tokens = self.capacity
        self.refill_rate = self.capacity / 60.0  # tokens per second
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

    def acquire(self):
        """Block until one request slot is available, then consume it."""
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait_time = (1 - self.tokens) / self.refill_rate
            time.sleep(min(wait_time, 1.0))

    def throttle_down(self, factor=0.5, floor=1):
        """React to a live 429 by shrinking effective capacity immediately.

        Self-correcting: no manual retuning needed when the account tier
        turns out to be lower than what was detected/configured.
        """
        with self._lock:
            self.capacity = max(floor, self.capacity * factor)
            self.refill_rate = self.capacity / 60.0
            self.tokens = min(self.tokens, self.capacity)
            return self.capacity

    def recover_up(self, factor=1.05):
        """Slowly restore capacity toward the configured limit after a
        period with no 429s, so a temporary throttle doesn't stick forever."""
        with self._lock:
            self.capacity = min(self.configured_capacity, self.capacity * factor)
            self.refill_rate = self.capacity / 60.0
            return self.capacity
