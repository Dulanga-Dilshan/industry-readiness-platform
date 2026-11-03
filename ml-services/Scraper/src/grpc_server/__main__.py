import asyncio

from .server import serve

try:
    asyncio.run(serve())
except KeyboardInterrupt:
    pass