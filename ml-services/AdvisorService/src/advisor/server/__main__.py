"""Entry point: gRPC (grpc.aio) and REST/SSE (uvicorn) in one event loop."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import grpc
import uvicorn

from advisor.app import build_app
from advisor.config.settings import Settings
from advisor.safety.redaction import install_log_redaction
from advisor.server.grpc_service import ADMIN_METHODS, AdvisorServicer
from advisor.server.proto import advisor_pb2_grpc as pbg
from advisor.server.rest.api import create_rest_app
from ml_common.auth import SecretInterceptor

log = logging.getLogger("advisor")


async def serve() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_log_redaction()
    problems = settings.validate()
    if problems:
        for p in problems:
            log.error("configuration problem: %s", p)
        sys.exit(2)
    app = build_app(settings)
    await app.start()

    server = grpc.aio.server(interceptors=[SecretInterceptor(settings.service_secret, settings.admin_key, ADMIN_METHODS)])
    pbg.add_AdvisorServiceServicer_to_server(AdvisorServicer(app), server)
    server.add_insecure_port(f"{settings.grpc_host}:{settings.grpc_port}")
    await server.start()
    log.info("gRPC listening on %s:%s", settings.grpc_host, settings.grpc_port)

    tasks = []
    uv = None
    try:
        if settings.enable_rest:
            uv = uvicorn.Server(uvicorn.Config(create_rest_app(app), host=settings.rest_host,port=settings.rest_port, log_level="warning"))
            uv.install_signal_handlers = lambda: None      
            tasks.append(asyncio.create_task(uv.serve()))
            log.info("REST/SSE listening on %s:%s", settings.rest_host, settings.rest_port)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass                                 
        await stop.wait()
    except asyncio.CancelledError:
        pass                                               
    finally:
        log.info("shutting down")
        if uv:
            uv.should_exit = True
        await server.stop(2)
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()