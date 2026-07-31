"""Redis-backed fixed-window rate limiting."""

from redis.asyncio import Redis


class RateLimiter:
    """Apply atomic fixed-window request limits through Redis."""

    def __init__(self, redis_url: str) -> None:
        """Initialize a lazy Redis client."""
        self._redis = Redis.from_url(redis_url, decode_responses=True)

    async def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return whether a request is permitted within the current window."""
        count = int(await self._redis.incr(key))
        if count == 1:
            await self._redis.expire(key, window_seconds)
        return count <= limit

    async def close(self) -> None:
        """Close the Redis connection pool."""
        await self._redis.aclose()
