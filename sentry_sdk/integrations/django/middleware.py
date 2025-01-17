"""
Create spans from Django middleware invocations
"""

from functools import wraps

from django import VERSION as DJANGO_VERSION

from sentry_sdk import Hub
from sentry_sdk.utils import ContextVar, transaction_from_function

_import_string_should_wrap_middleware = ContextVar(
    "import_string_should_wrap_middleware"
)

if DJANGO_VERSION < (1, 7):
    import_string_name = "import_by_path"
else:
    import_string_name = "import_string"


def patch_django_middlewares():
    from django.core.handlers import base

    old_import_string = getattr(base, import_string_name)

    def sentry_patched_import_string(dotted_path):
        rv = old_import_string(dotted_path)

        if _import_string_should_wrap_middleware.get(None):
            rv = _wrap_middleware(rv, dotted_path)

        return rv

    setattr(base, import_string_name, sentry_patched_import_string)

    old_load_middleware = base.BaseHandler.load_middleware

    def sentry_patched_load_middleware(self):
        _import_string_should_wrap_middleware.set(True)
        try:
            return old_load_middleware(self)
        finally:
            _import_string_should_wrap_middleware.set(False)

    base.BaseHandler.load_middleware = sentry_patched_load_middleware


def _wrap_middleware(middleware, middleware_name):
    from sentry_sdk.integrations.django import DjangoIntegration

    def _get_wrapped_method(old_method):
        @wraps(old_method)
        def sentry_wrapped_method(*args, **kwargs):
            hub = Hub.current
            integration = hub.get_integration(DjangoIntegration)
            if integration is None or not integration.middleware_spans:
                return old_method(*args, **kwargs)

            function_name = transaction_from_function(old_method)

            description = middleware_name
            function_basename = getattr(old_method, "__name__", None)
            if function_basename:
                description = "{}.{}".format(description, function_basename)

            with hub.start_span(
                op="django.middleware", description=description
            ) as span:
                span.set_tag("django.function_name", function_name)
                span.set_tag("django.middleware_name", middleware_name)
                return old_method(*args, **kwargs)

        return sentry_wrapped_method

    class SentryWrappingMiddleware(object):
        def __init__(self, *args, **kwargs):
            self._inner = middleware(*args, **kwargs)
            self._call_method = None

        # We need correct behavior for `hasattr()`, which we can only determine
        # when we have an instance of the middleware we're wrapping.
        def __getattr__(self, method_name):
            if method_name not in (
                "process_request",
                "process_view",
                "process_template_response",
                "process_response",
                "process_exception",
            ):
                raise AttributeError()

            old_method = getattr(self._inner, method_name)
            rv = _get_wrapped_method(old_method)
            self.__dict__[method_name] = rv
            return rv

        def __call__(self, *args, **kwargs):
            if self._call_method is None:
                self._call_method = _get_wrapped_method(self._inner.__call__)
            return self._call_method(*args, **kwargs)

    if hasattr(middleware, "__name__"):
        SentryWrappingMiddleware.__name__ = middleware.__name__

    return SentryWrappingMiddleware
