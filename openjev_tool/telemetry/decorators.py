"""Tracing decorators and context managers.

Provides @traced decorator and trace_span context manager for
easy instrumentation of functions and code blocks.

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from openjev_tool.telemetry.service import TelemetryService

if TYPE_CHECKING:
    from opentelemetry.trace import Span

F = TypeVar("F", bound=Callable[..., Any])


def traced(
    name: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """Decorator to trace function execution.

    Creates a span around the decorated function. Automatically records
    exceptions and sets error status on failure.

    Args:
        name: Span name. Defaults to function name.
        attributes: Additional span attributes.

    Returns:
        Decorated function.

    Example:
        >>> @traced("process_data")
        ... def process_data(items: list) -> dict:
        ...     return {"count": len(items)}

        >>> @traced(attributes={"operation.type": "batch"})
        ... def batch_process():
        ...     pass
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            service = TelemetryService.get_instance()

            if not service.is_enabled:
                return func(*args, **kwargs)

            span_name = name or func.__name__
            tracer = service.tracer

            with tracer.start_as_current_span(span_name, attributes=attributes) as span:
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    _record_exception(span, e)
                    raise

        return wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Generator[Span | None]:
    """Context manager for tracing code blocks.

    Creates a span around the code block. Automatically records
    exceptions and sets error status on failure.

    Args:
        name: Span name.
        attributes: Additional span attributes.

    Yields:
        The created span, or None if telemetry is disabled.

    Example:
        >>> with trace_span("database_query", {"db.system": "postgres"}):
        ...     result = db.execute(query)

        >>> with trace_span("file_processing") as span:
        ...     if span:
        ...         span.set_attribute("file.size", file_size)
        ...     process_file(path)
    """
    service = TelemetryService.get_instance()

    if not service.is_enabled:
        yield None
        return

    tracer = service.tracer

    with tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            yield span
        except Exception as e:
            _record_exception(span, e)
            raise


def _record_exception(span: Span, exception: Exception) -> None:
    """Record exception on span and set error status.

    Args:
        span: The span to record the exception on.
        exception: The exception that occurred.
    """
    from opentelemetry.trace import Status, StatusCode

    span.record_exception(exception)
    span.set_status(Status(StatusCode.ERROR, str(exception)))
