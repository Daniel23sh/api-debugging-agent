import os
from collections.abc import Sequence

from openinference.instrumentation import REDACTED_VALUE, TraceConfig, TracerProvider
from openinference.instrumentation.openai import OpenAIInstrumentor
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import NoOpTracerProvider, Status, StatusCode, Tracer
from phoenix.otel import PROJECT_NAME, HTTPSpanExporter

DEFAULT_PROJECT_NAME = "apilens"
DEFAULT_SERVICE_NAME = "apilens-agent-service"
INSTRUMENTATION_SCOPE = "agent_service"

_NOOP_PROVIDER = NoOpTracerProvider()
_PRIVATE_ERROR_FIELDS = {"exception.message", "exception.stacktrace"}


def _sanitize_llm_error(span: ReadableSpan) -> ReadableSpan:
    if (
        span.status.status_code is not StatusCode.ERROR
        or (span.attributes or {}).get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
        != OpenInferenceSpanKindValues.LLM.value
    ):
        return span

    # Instrumentation's I/O hiding does not cover exception text or error status.
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes={
            key: REDACTED_VALUE if key in _PRIVATE_ERROR_FIELDS else value
            for key, value in (span.attributes or {}).items()
        },
        events=[
            Event(
                name=event.name,
                timestamp=event.timestamp,
                attributes={
                    key: REDACTED_VALUE if key in _PRIVATE_ERROR_FIELDS else value
                    for key, value in (event.attributes or {}).items()
                },
            )
            for event in span.events
        ],
        links=span.links,
        kind=span.kind,
        status=Status(StatusCode.ERROR, "LLM request failed"),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class _ErrorSanitizingExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._exporter.export([_sanitize_llm_error(span) for span in spans])

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)

    def shutdown(self) -> None:
        self._exporter.shutdown()


def configure_tracing(
    *, span_exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Create an app-owned provider, or disable tracing when no exporter is configured."""
    collector_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
    if span_exporter is None and not collector_endpoint:
        return None

    project_name = os.getenv("PHOENIX_PROJECT_NAME", "").strip() or DEFAULT_PROJECT_NAME
    service_name = os.getenv("OTEL_SERVICE_NAME", "").strip() or DEFAULT_SERVICE_NAME
    resource = Resource.create(
        {PROJECT_NAME: project_name, "service.name": service_name}
    )

    provider = TracerProvider(resource=resource, shutdown_on_exit=False)
    if span_exporter is None:
        processor = BatchSpanProcessor(_ErrorSanitizingExporter(HTTPSpanExporter()))
    else:
        processor = SimpleSpanProcessor(_ErrorSanitizingExporter(span_exporter))
    provider.add_span_processor(processor)
    return provider


def get_tracer(provider: TracerProvider | None) -> Tracer:
    return (provider or _NOOP_PROVIDER).get_tracer(INSTRUMENTATION_SCOPE)


def configure_openai_instrumentation(
    provider: TracerProvider | None,
) -> OpenAIInstrumentor | None:
    if provider is None:
        return None
    instrumentor = OpenAIInstrumentor()
    instrumentor.instrument(
        tracer_provider=provider,
        config=TraceConfig(hide_inputs=True, hide_outputs=True),
    )
    return instrumentor


def shutdown_openai_instrumentation(
    instrumentor: OpenAIInstrumentor | None,
) -> None:
    if instrumentor is not None:
        instrumentor.uninstrument()


def shutdown_tracing(provider: TracerProvider | None) -> None:
    if provider is not None:
        provider.force_flush()
        provider.shutdown()
