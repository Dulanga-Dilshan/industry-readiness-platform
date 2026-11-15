"""Shared-secret and admin-key checks (constant time) plus a gRPC interceptor."""
from __future__ import annotations

import hmac
import logging

import grpc

log = logging.getLogger("ml_common.auth")
audit_log = logging.getLogger("ml_common.audit")

SECRET_HEADER = "x-service-secret"
ADMIN_HEADER = "x-admin-key"


def secrets_match(provided: str | None, expected: str | None) -> bool:
    """Constant-time compare. An empty expected secret never matches."""
    if not expected or provided is None:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


class SecretInterceptor(grpc.aio.ServerInterceptor):
    """Checks `x-service-secret` on every call; `x-admin-key` on admin methods."""

    def __init__(self, service_secret: str, admin_key: str, admin_methods: set[str]):
        self._secret = service_secret
        self._admin = admin_key
        self._admin_methods = admin_methods

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None:
            return handler
        method = handler_call_details.method.rsplit("/", 1)[-1]
        needs_admin = method in self._admin_methods

        def check(context) -> str | None:
            md = {k: v for k, v in (context.invocation_metadata() or ())}
            if not secrets_match(md.get(SECRET_HEADER), self._secret):
                return "bad or missing service secret"
            if needs_admin:
                if not secrets_match(md.get(ADMIN_HEADER), self._admin):
                    audit_log.warning("admin call rejected: %s", method)
                    return "bad or missing admin key"
                audit_log.info("admin call: %s", method)
            return None

        if handler.unary_unary:
            inner = handler.unary_unary

            async def uu(request, context):
                problem = check(context)
                if problem:
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, problem)
                return await inner(request, context)

            return grpc.unary_unary_rpc_method_handler(
                uu, request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer)
        if handler.unary_stream:
            inner_s = handler.unary_stream

            async def us(request, context):
                problem = check(context)
                if problem:
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, problem)
                async for item in inner_s(request, context):
                    yield item

            return grpc.unary_stream_rpc_method_handler(
                us, request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer)
        return handler
