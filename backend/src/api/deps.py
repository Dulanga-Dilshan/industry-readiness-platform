from typing import Annotated
from fastapi import Depends
from redis.asyncio import Redis
from db.redis import get_redis

RedisDep = Annotated[Redis, Depends(get_redis)]
