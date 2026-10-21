"""P3：Provider / 域名熔断器。"""
from app.core.circuit_breaker import CircuitBreaker


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_opens_after_threshold_and_cooldown():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_seconds=60, clock=clock)

    for _ in range(2):
        breaker.record_failure("web:tavily")
    assert breaker.allow("web:tavily") is True
    assert breaker.failure_count("web:tavily") == 2

    breaker.record_failure("web:tavily")
    assert breaker.is_open("web:tavily") is True
    assert breaker.allow("web:tavily") is False

    clock.advance(59)
    assert breaker.allow("web:tavily") is False

    clock.advance(2)
    assert breaker.allow("web:tavily") is True
    assert breaker.failure_count("web:tavily") == 0


def test_success_resets_failures():
    breaker = CircuitBreaker(threshold=3, cooldown_seconds=60)
    breaker.record_failure("web:bing")
    breaker.record_failure("web:bing")
    breaker.record_success("web:bing")
    assert breaker.failure_count("web:bing") == 0


def test_keys_are_independent():
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=60)
    breaker.record_failure("web:tavily")
    breaker.record_failure("web:tavily")
    assert breaker.is_open("web:tavily") is True
    assert breaker.is_open("web:bing") is False
