"""SlidingWindowRateLimiter: windows, Retry-After, pruning, threads (OPS-44)."""

import threading

import pytest

from questly.core.auth.service import RateLimitedError
from questly.integrations.rate_limit import RateLimit, SlidingWindowRateLimiter


class ManualClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _limiter(clock: ManualClock) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(
        {
            "login": RateLimit(limit=3, window_seconds=60),
            "signup": RateLimit(limit=1, window_seconds=600),
        },
        clock=clock,
    )


def _accepted(limiter: SlidingWindowRateLimiter, bucket: str, key: str) -> bool:
    try:
        limiter.hit(bucket, key)
    except RateLimitedError:
        return False
    return True


def test_refuses_the_hit_after_the_limit_within_the_window() -> None:
    clock = ManualClock()
    limiter = _limiter(clock)

    results = [_accepted(limiter, "login", "1.2.3.4") for _ in range(4)]

    assert results == [True, True, True, False]


def test_a_hit_counts_again_once_the_oldest_leaves_the_window() -> None:
    clock = ManualClock()
    limiter = _limiter(clock)
    limiter.hit("login", "1.2.3.4")
    clock.now += 30
    limiter.hit("login", "1.2.3.4")
    limiter.hit("login", "1.2.3.4")

    clock.now += 29.5
    assert not _accepted(limiter, "login", "1.2.3.4")
    clock.now += 0.5
    assert _accepted(limiter, "login", "1.2.3.4")
    assert not _accepted(limiter, "login", "1.2.3.4")


def test_retry_after_is_the_rounded_up_wait_for_the_oldest_hit() -> None:
    clock = ManualClock()
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.hit("login", "1.2.3.4")
    clock.now += 20.4

    with pytest.raises(RateLimitedError) as refused:
        limiter.hit("login", "1.2.3.4")

    assert refused.value.retry_after_seconds == 40


def test_retry_after_is_at_least_one_second() -> None:
    clock = ManualClock()
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.hit("login", "1.2.3.4")
    clock.now += 59.9

    with pytest.raises(RateLimitedError) as refused:
        limiter.hit("login", "1.2.3.4")

    assert refused.value.retry_after_seconds == 1


def test_refused_hits_do_not_extend_the_wait() -> None:
    clock = ManualClock()
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.hit("login", "1.2.3.4")
    for _ in range(50):
        clock.now += 1
        assert not _accepted(limiter, "login", "1.2.3.4")

    clock.now += 10

    assert _accepted(limiter, "login", "1.2.3.4")


def test_buckets_and_keys_are_counted_separately() -> None:
    clock = ManualClock()
    limiter = _limiter(clock)
    limiter.hit("signup", "1.2.3.4")

    assert not _accepted(limiter, "signup", "1.2.3.4")
    assert _accepted(limiter, "signup", "5.6.7.8")
    assert _accepted(limiter, "login", "1.2.3.4")


def test_keys_whose_hits_expired_are_dropped() -> None:
    clock = ManualClock()
    limiter = _limiter(clock)
    for address in range(100):
        limiter.hit("login", f"10.0.0.{address}")
    assert limiter.tracked_keys() == 100

    clock.now += 600
    limiter.hit("login", "10.0.1.1")

    assert limiter.tracked_keys() == 1


def test_an_unknown_bucket_is_an_error_not_unlimited() -> None:
    limiter = _limiter(ManualClock())

    with pytest.raises(KeyError):
        limiter.hit("upload", "1.2.3.4")


def test_concurrent_hits_on_one_key_accept_exactly_the_limit() -> None:
    limiter = SlidingWindowRateLimiter(
        {"login": RateLimit(limit=25, window_seconds=60)}, clock=ManualClock()
    )
    start = threading.Barrier(20)
    accepted: list[bool] = []
    record = threading.Lock()

    def hammer() -> None:
        start.wait()
        for _ in range(10):
            result = _accepted(limiter, "login", "1.2.3.4")
            with record:
                accepted.append(result)

    threads = [threading.Thread(target=hammer) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert accepted.count(True) == 25
    assert accepted.count(False) == 175
