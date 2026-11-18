import time
from collections.abc import Callable

from fastapi import HTTPException, Request

from app.core.config import get_settings
from app.core.redis import get_redis


def resolve_client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """Client IP for rate-limit keys, honoring `trusted_proxy_hops` trusted reverse proxies.

    Each trusted proxy appends its own observed peer address to the right end of
    X-Forwarded-For, so the Nth entry from the right is always a real, unspoofable
    observation rather than something the original client could have written into
    the header itself. A chain shorter than the configured hop count means the
    trust setting does not match what actually reached us, so it is not trusted.
    """
    if trusted_proxy_hops <= 0:
        return request.client.host if request.client else "unknown"

    forwarded_for = request.headers.get("x-forwarded-for")
    hops = [hop.strip() for hop in forwarded_for.split(",")] if forwarded_for else []
    if len(hops) < trusted_proxy_hops:
        return "unknown"

    return hops[-trusted_proxy_hops]


class RateLimiter:
    """Sliding window per client IP, counted in Redis.

    `max_requests` may be a zero-argument callable, so a ceiling kept in
    settings is read on every request instead of frozen at import time. A
    ceiling of zero or less turns the limiter off.

    Requests are keyed by `scope`, or by the request path when there is none.
    A scope shared by several routes, or by every id a path template takes,
    puts them in one bucket per IP, so moving to another route or another id
    does not hand out a fresh budget.
    """

    def __init__(
        self,
        max_requests: int | Callable[[], int],
        window_seconds: int,
        scope: str | None = None,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.scope = scope

    def ceiling(self) -> int:
        limit = self.max_requests
        if isinstance(limit, int):
            return limit
        return limit()

    async def __call__(self, request: Request) -> None:
        limit = self.ceiling()
        if limit <= 0:
            return

        client_ip = resolve_client_ip(request, get_settings().trusted_proxy_hops)
        key = f"rate_limit:{self.scope or request.url.path}:{client_ip}"

        r = await get_redis()
        now = time.time()
        window_start = now - self.window_seconds

        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, self.window_seconds)
        results = await pipe.execute()

        request_count = results[1]

        if request_count >= limit:
            retry_after = self.window_seconds
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )


login_rate_limit = RateLimiter(max_requests=5, window_seconds=60)
register_rate_limit = RateLimiter(max_requests=3, window_seconds=3600)
password_reset_rate_limit = RateLimiter(max_requests=3, window_seconds=3600)


def _agents_chat_ceiling() -> int:
    return get_settings().agents_chat_rate_limit_per_minute


# Every assistant message spends provider tokens, so the chat has a ceiling of
# its own, read from settings so a demo can be tighter than a self-hosted
# instance. All agents share one bucket: per-path keys would give a caller a
# fresh minute every time it switched agent id.
agents_chat_rate_limit = RateLimiter(
    max_requests=_agents_chat_ceiling,
    window_seconds=60,
    scope="agents_chat",
)
