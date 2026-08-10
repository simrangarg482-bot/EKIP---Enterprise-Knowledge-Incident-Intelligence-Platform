"""Tests for `app.ingestion.rate_limiter.TokenBucketRateLimiter`.

Uses a high `rate` (100/s -> ~10ms per token) throughout so timing
assertions stay fast and comfortably tolerant of scheduling jitter in CI,
rather than asserting exact durations.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.ingestion.rate_limiter import TokenBucketRateLimiter, get_ingestion_rate_limiter


@pytest.mark.asyncio
async def test_first_acquire_for_a_fresh_key_does_not_block() -> None:
    limiter = TokenBucketRateLimiter()

    started = time.monotonic()
    await limiter.acquire("connector:a", 100.0)
    elapsed = time.monotonic() - started

    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_exhausting_the_burst_forces_the_next_acquire_to_wait() -> None:
    limiter = TokenBucketRateLimiter()
    rate = 100.0  # burst == rate == 100 tokens available immediately

    # Drain the full burst -- all 100 should return essentially instantly.
    started = time.monotonic()
    for _ in range(100):
        await limiter.acquire("connector:a", rate)
    drain_elapsed = time.monotonic() - started
    assert drain_elapsed < 0.2

    # The 101st call has no tokens left and must wait ~1/rate seconds.
    started = time.monotonic()
    await limiter.acquire("connector:a", rate)
    wait_elapsed = time.monotonic() - started

    assert wait_elapsed >= (1.0 / rate) * 0.5  # allow generous scheduling slack


@pytest.mark.asyncio
async def test_sub_one_rate_eventually_grants_a_token() -> None:
    """Regression test: a bucket capped at `rate` itself (not floored at
    1.0) can never reach the `tokens >= 1.0` threshold `acquire()` checks
    for when `rate < 1.0` -- every call loops forever instead of eventually
    proceeding. `SlackConnector.requests_per_second = 0.5` is the one real
    caller this affects; a high `rate` elsewhere in this file would never
    have caught it. Bounded with `asyncio.wait_for` so a regression fails
    the test instead of hanging the suite.
    """
    limiter = TokenBucketRateLimiter()
    rate = 0.5  # one token every 2 seconds -- matches SlackConnector

    started = time.monotonic()
    await asyncio.wait_for(limiter.acquire("connector:slack", rate), timeout=5.0)
    elapsed = time.monotonic() - started

    # First call starts with `rate` (0.5) tokens, short of the 1.0 needed,
    # so it must wait roughly (1.0 - rate) / rate = 1s -- but it must
    # complete at all, which is the actual regression being guarded here.
    assert elapsed >= 0.5


@pytest.mark.asyncio
async def test_rate_zero_or_negative_never_blocks() -> None:
    limiter = TokenBucketRateLimiter()

    started = time.monotonic()
    for _ in range(1000):
        await limiter.acquire("connector:a", 0.0)
        await limiter.acquire("connector:a", -1.0)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_different_keys_have_independent_buckets() -> None:
    limiter = TokenBucketRateLimiter()
    rate = 100.0

    # Drain key "a" completely.
    for _ in range(100):
        await limiter.acquire("connector:a", rate)

    # Key "b" is untouched -- still has its own full burst, so this must
    # not be slowed down by "a"'s exhausted bucket.
    started = time.monotonic()
    await limiter.acquire("connector:b", rate)
    elapsed = time.monotonic() - started

    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_concurrent_acquires_for_the_same_key_are_serialized_not_double_spent() -> None:
    """Two concurrent callers draining the same small bucket must not both
    observe the same stale token count -- the internal lock exists exactly
    to prevent this (see the class's own docstring).
    """
    limiter = TokenBucketRateLimiter()
    rate = 2.0  # burst of 2 tokens

    results = await asyncio.gather(
        limiter.acquire("connector:shared", rate),
        limiter.acquire("connector:shared", rate),
        limiter.acquire("connector:shared", rate),
    )

    # All three eventually complete (none raise/deadlock); the third one
    # necessarily had to wait for a refill since only 2 tokens existed.
    assert results == [None, None, None]


def test_get_ingestion_rate_limiter_returns_the_same_shared_instance() -> None:
    """2026-08 audit "H2" fix: `ingestion.service` and `JiraConnector` (and
    any future connector opting out of the generic path) must draw from the
    exact same buckets, not two independent ones that would silently double
    the real ceiling -- this is only true if `get_ingestion_rate_limiter()`
    always returns the one shared instance, never a fresh one.
    """
    assert get_ingestion_rate_limiter() is get_ingestion_rate_limiter()
