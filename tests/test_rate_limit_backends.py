"""
Where the rate-limit counters live, and what happens when that place is gone.

The Redis backend exists for one reason: the in-memory window is PER PROCESS,
so two web workers mean two windows and twice the effective limit. On Render
that is the normal deployment, which makes the memory backend a limiter that
quietly enforces double what it says.

No Redis server runs in this suite, so the client is faked. That is enough to
test what is actually worth testing here — which backend gets chosen, what
commands the Redis path issues, and above all that a Redis outage degrades to
counting in memory rather than failing every request. A limiter that 500s when
its store blips has turned a defence into an outage.
"""

import asyncio

import pytest

from server import middleware


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# --- Choosing a backend ---------------------------------------------------
def test_no_redis_url_means_the_in_memory_window():
    backend = middleware.make_backend("")
    assert isinstance(backend, middleware.MemoryWindow)
    assert backend.name == "memory"


def test_a_redis_url_selects_the_shared_window():
    backend = middleware.make_backend("redis://localhost:6379/0")
    assert isinstance(backend, middleware.RedisWindow)
    assert backend.name == "redis"


def test_the_middleware_takes_the_configured_backend_by_default():
    app = middleware.RateLimitMiddleware(lambda scope, receive, send: None)
    assert isinstance(app.backend,
                      (middleware.MemoryWindow, middleware.RedisWindow))


# --- The in-memory window -------------------------------------------------
def test_the_memory_window_admits_up_to_the_limit_then_stops():
    window = middleware.MemoryWindow()
    verdicts = [run(window.hit("k", 3, 60))[0] for _ in range(5)]
    assert verdicts == [True, True, True, False, False]


def test_the_memory_window_keeps_separate_keys_apart():
    window = middleware.MemoryWindow()
    for _ in range(3):
        run(window.hit("a", 3, 60))
    assert run(window.hit("a", 3, 60))[0] is False
    assert run(window.hit("b", 3, 60))[0] is True


def test_a_refused_request_is_told_when_to_come_back():
    window = middleware.MemoryWindow()
    for _ in range(2):
        run(window.hit("k", 2, 60))
    allowed, retry_after = run(window.hit("k", 2, 60))
    assert allowed is False
    assert 1 <= retry_after <= 61


# --- The Redis window, against a fake client ------------------------------
class FakePipeline:
    def __init__(self, store, fail=False):
        self.store = store
        self.fail = fail
        self.calls = []

    def zremrangebyscore(self, key, lo, hi):
        self.calls.append(("zremrangebyscore", key))
        self.store.setdefault(key, [])
        self.store[key] = [s for s in self.store[key] if s > hi]

    def zcard(self, key):
        self.calls.append(("zcard", key))

    def zadd(self, key, mapping):
        self.calls.append(("zadd", key))
        self.store.setdefault(key, []).extend(mapping.values())

    def expire(self, key, seconds):
        self.calls.append(("expire", key))

    async def execute(self):
        if self.fail:
            raise ConnectionError("redis is not answering")
        key = self.calls[0][1]
        # zcard is evaluated BEFORE this request's own zadd lands, which is
        # what makes the limit "how many were already there".
        return [0, len(self.store.get(key, [])) - 1, 1, True]


class FakeRedis:
    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail
        self.pipelines = 0

    def pipeline(self, transaction=True):
        self.pipelines += 1
        return FakePipeline(self.store, fail=self.fail)


def _redis_window(fail=False):
    window = middleware.RedisWindow("redis://fake")
    window._client = FakeRedis(fail=fail)
    return window


def test_the_redis_window_admits_up_to_the_limit_then_stops():
    window = _redis_window()
    verdicts = [run(window.hit("k", 3, 60))[0] for _ in range(5)]
    assert verdicts == [True, True, True, False, False]


def test_the_redis_window_issues_one_round_trip_per_request():
    """Four commands, one pipeline. A limiter that costs four round trips per
    request is a latency tax on every write in the product."""
    window = _redis_window()
    run(window.hit("k", 5, 60))
    assert window._client.pipelines == 1


def test_the_redis_key_is_namespaced():
    window = _redis_window()
    run(window.hit("auth:ip:1.2.3.4", 5, 60))
    assert list(window._client.store) == ["ratelimit:auth:ip:1.2.3.4"]


def test_two_requests_in_the_same_instant_are_both_counted():
    """Members are timestamp PLUS randomness. Keyed on the timestamp alone,
    two requests in the same microsecond would collapse into one sorted-set
    member and one of them would go uncounted."""
    window = _redis_window()
    for _ in range(4):
        run(window.hit("k", 10, 60))
    assert len(window._client.store["ratelimit:k"]) == 4


# --- Degrading gracefully -------------------------------------------------
def test_a_redis_outage_falls_back_to_counting_in_memory(capsys):
    window = _redis_window(fail=True)
    allowed, _ = run(window.hit("k", 2, 60))
    assert allowed is True, "an unreachable Redis must not refuse the request"
    assert window.degraded is True
    assert "falling back" in capsys.readouterr().out


def test_the_fallback_still_enforces_the_limit():
    """Degraded is not disabled: one process still meters itself."""
    window = _redis_window(fail=True)
    verdicts = [run(window.hit("k", 3, 60))[0] for _ in range(5)]
    assert verdicts == [True, True, True, False, False]


def test_an_outage_is_logged_once_not_once_per_request(capsys):
    """Redis being down is down for every request. A line each would bury the
    log at exactly the moment somebody is reading it."""
    window = _redis_window(fail=True)
    for _ in range(5):
        run(window.hit("k", 100, 60))
    assert capsys.readouterr().out.count("falling back") == 1


def test_a_failed_client_is_dropped_so_the_next_call_reconnects():
    window = _redis_window(fail=True)
    run(window.hit("k", 5, 60))
    assert window._client is None


# --- Identity -------------------------------------------------------------
class _Req:
    def __init__(self, headers=None, host="10.0.0.1"):
        import types

        self.headers = headers or {}
        self.client = types.SimpleNamespace(host=host)


def test_an_anonymous_caller_is_metered_by_ip():
    """Login has no user id to meter by — finding one is what the attacker is
    trying to do."""
    assert middleware.RateLimitMiddleware.identity(_Req()) == "ip:10.0.0.1"


def test_a_proxied_caller_is_metered_by_the_original_ip():
    request = _Req({"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    assert middleware.RateLimitMiddleware.identity(request) == "ip:203.0.113.9"


def test_an_authenticated_caller_is_metered_by_account():
    """Otherwise one office NAT throttles everyone behind it for the heaviest
    user among them."""
    import auth

    token = auth.create_access_token("tester")
    request = _Req({"authorization": f"Bearer {token}"})
    assert middleware.RateLimitMiddleware.identity(request) == "u:tester"


def test_an_unusable_token_falls_back_to_the_ip():
    """The token is a grouping key here, not a credential — it is read before
    the auth gate and never trusted. A forged one buys being metered as
    somebody else, and the gate turns it away regardless."""
    request = _Req({"authorization": "Bearer nonsense.not.a.token"})
    assert middleware.RateLimitMiddleware.identity(request) == "ip:10.0.0.1"


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer "])
def test_a_missing_or_wrong_scheme_falls_back_to_the_ip(header):
    request = _Req({"authorization": header} if header else {})
    assert middleware.RateLimitMiddleware.identity(request) == "ip:10.0.0.1"
