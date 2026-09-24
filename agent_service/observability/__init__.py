import os

from openinference.instrumentation import TraceConfig, TracerProvider
from openinference.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import NoOpTracerProvider, Tracer
from phoenix.otel import PROJECT_NAME, register

DEFAULT_PROJECT_NAME = "apilens"
DEFAULT_SERVICE_NAME = "apilens-agent-service"
INSTRUMENTATION_SCOPE = "agent_service"

_NOOP_PROVIDER = NoOpTracerProvider()


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

    if span_exporter is not None:
        provider = TracerProvider(resource=resource, shutdown_on_exit=False)
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        return provider

    return register(
        project_name=project_name,
        batch=True,
        set_global_tracer_provider=False,
        protocol="http/protobuf",
        auto_instrument=False,
        verbose=False,
        resource=resource,
        shutdown_on_exit=False,
    )


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
