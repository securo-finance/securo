import time

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
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(self, request: Request) -> None:
        client_ip = resolve_client_ip(request, get_settings().trusted_proxy_hops)
        key = f"rate_limit:{request.url.path}:{client_ip}"

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

        if request_count >= self.max_requests:
            retry_after = self.window_seconds
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )


login_rate_limit = RateLimiter(max_requests=5, window_seconds=60)
register_rate_limit = RateLimiter(max_requests=3, window_seconds=3600)
password_reset_rate_limit = RateLimiter(max_requests=3, window_seconds=3600)
