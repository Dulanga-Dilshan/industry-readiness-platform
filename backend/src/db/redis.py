from typing import AsyncGenerator
from redis.asyncio import ConnectionPool, Redis
from core.config import REDIS_URL

pool = ConnectionPool.from_url(
    REDIS_URL,
    decode_responses=True,
    max_connections=10,
)

async def get_redis() -> AsyncGenerator[Redis, None]:
    client = Redis(connection_pool=pool)
    try:
        yield client
    finally:
        await client.aclose()
