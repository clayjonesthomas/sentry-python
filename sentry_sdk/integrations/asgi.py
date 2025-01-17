"""
An ASGI middleware.

Based on Tom Christie's `sentry-asgi <https://github.com/encode/sentry-asgi>`_.
"""

import functools
import urllib

from sentry_sdk._types import MYPY
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.utils import ContextVar, event_from_exception, transaction_from_function
from sentry_sdk.tracing import Span

if MYPY:
    from typing import Dict
    from typing import Any


_asgi_middleware_applied = ContextVar("sentry_asgi_middleware_applied")


def _capture_exception(hub, exc):
    # type: (Hub, Any) -> None

    # Check client here as it might have been unset while streaming response
    if hub.client is not None:
        event, hint = event_from_exception(
            exc,
            client_options=hub.client.options,
            mechanism={"type": "asgi", "handled": False},
        )
        hub.capture_event(event, hint=hint)


class SentryAsgiMiddleware:
    __slots__ = ("app",)

    def __init__(self, app):
        self.app = app

    def __call__(self, scope, receive=None, send=None):
        if receive is None or send is None:

            async def run_asgi2(receive, send):
                return await self._run_app(
                    scope, lambda: self.app(scope)(receive, send)
                )

            return run_asgi2
        else:
            return self._run_app(scope, lambda: self.app(scope, receive, send))

    async def _run_app(self, scope, callback):
        if _asgi_middleware_applied.get(False):
            return await callback()

        _asgi_middleware_applied.set(True)
        try:
            hub = Hub(Hub.current)
            with hub:
                with hub.configure_scope() as sentry_scope:
                    sentry_scope.clear_breadcrumbs()
                    sentry_scope._name = "asgi"
                    processor = functools.partial(
                        self.event_processor, asgi_scope=scope
                    )
                    sentry_scope.add_event_processor(processor)

                if scope["type"] in ("http", "websocket"):
                    span = Span.continue_from_headers(dict(scope["headers"]))
                    span.op = "{}.server".format(scope["type"])
                else:
                    span = Span()
                    span.op = "asgi.server"

                span.set_tag("asgi.type", scope["type"])
                span.transaction = "generic ASGI request"

                with hub.start_span(span) as span:
                    try:
                        return await callback()
                    except Exception as exc:
                        _capture_exception(hub, exc)
                        raise exc from None
        finally:
            _asgi_middleware_applied.set(False)

    def event_processor(self, event, hint, asgi_scope):
        request_info = event.setdefault("request", {})

        if asgi_scope["type"] in ("http", "websocket"):
            request_info["url"] = self.get_url(asgi_scope)
            request_info["method"] = asgi_scope["method"]
            request_info["headers"] = _filter_headers(self.get_headers(asgi_scope))
            request_info["query_string"] = self.get_query(asgi_scope)

        if asgi_scope.get("client") and _should_send_default_pii():
            request_info["env"] = {"REMOTE_ADDR": asgi_scope["client"][0]}

        if asgi_scope.get("endpoint"):
            # Webframeworks like Starlette mutate the ASGI env once routing is
            # done, which is sometime after the request has started. If we have
            # an endpoint, overwrite our path-based transaction name.
            event["transaction"] = self.get_transaction(asgi_scope)
        return event

    def get_url(self, scope):
        """
        Extract URL from the ASGI scope, without also including the querystring.
        """
        scheme = scope.get("scheme", "http")
        server = scope.get("server", None)
        path = scope.get("root_path", "") + scope["path"]

        for key, value in scope["headers"]:
            if key == b"host":
                host_header = value.decode("latin-1")
                return "%s://%s%s" % (scheme, host_header, path)

        if server is not None:
            host, port = server
            default_port = {"http": 80, "https": 443, "ws": 80, "wss": 443}[scheme]
            if port != default_port:
                return "%s://%s:%s%s" % (scheme, host, port, path)
            return "%s://%s%s" % (scheme, host, path)
        return path

    def get_query(self, scope):
        """
        Extract querystring from the ASGI scope, in the format that the Sentry protocol expects.
        """
        return urllib.parse.unquote(scope["query_string"].decode("latin-1"))

    def get_headers(self, scope):
        """
        Extract headers from the ASGI scope, in the format that the Sentry protocol expects.
        """
        headers = {}  # type: Dict[str, str]
        for raw_key, raw_value in scope["headers"]:
            key = raw_key.decode("latin-1")
            value = raw_value.decode("latin-1")
            if key in headers:
                headers[key] = headers[key] + ", " + value
            else:
                headers[key] = value
        return headers

    def get_transaction(self, scope):
        """
        Return a transaction string to identify the routed endpoint.
        """
        return transaction_from_function(scope["endpoint"])
