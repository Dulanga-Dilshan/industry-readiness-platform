import asyncio
import logging

import grpc

from . import config
from .proto import jobs_pb2_grpc as pb_grpc
from .servicers import ClassifierServicer, ScraperServicer

log = logging.getLogger(__name__)


def build_server():
    size = config.MAX_MESSAGE_MB * 1024 * 1024
    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", size),
            ("grpc.max_receive_message_length", size),
        ]
    )
    pb_grpc.add_ScraperServiceServicer_to_server(ScraperServicer(), server)
    pb_grpc.add_ClassifierServiceServicer_to_server(ClassifierServicer(), server)
    return server


async def serve():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = build_server()
    # Insecure is fine inside a private Docker network. Add TLS before exposing it.
    server.add_insecure_port(f"{config.GRPC_HOST}:{config.GRPC_PORT}")
    await server.start()
    log.info("gRPC server listening on port %s", config.GRPC_PORT)

    tasks = [asyncio.create_task(server.wait_for_termination())]
    if config.ENABLE_REST:
        import uvicorn

        from .rest_api import app

        rest = uvicorn.Server(
            uvicorn.Config(app, host=config.REST_HOST, port=config.REST_PORT, log_level="info")
        )
        tasks.append(asyncio.create_task(rest.serve()))
        log.info("REST test API on http://%s:%s/docs", config.REST_HOST, config.REST_PORT)

    try:
        await asyncio.gather(*tasks)
    finally:
        await server.stop(grace=5)
