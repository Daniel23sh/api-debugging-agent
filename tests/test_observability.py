from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from phoenix.otel import PROJECT_NAME

from agent_service.main import create_app
from agent_service.observability import (
    DEFAULT_PROJECT_NAME,
    DEFAULT_SERVICE_NAME,
    configure_tracing,
    get_tracer,
    shutdown_tracing,
)


class RecordingExporter(InMemorySpanExporter):
    def __init__(self) -> None:
        super().__init__()
        self.shutdown_count = 0

    def shutdown(self) -> None:
        self.shutdown_count += 1
        super().shutdown()


def test_tracing_is_disabled_without_a_collector(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)

    assert configure_tracing() is None
    shutdown_tracing(None)


def test_tracing_supports_an_in_memory_exporter(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    provider = configure_tracing(span_exporter=exporter)

    assert provider is not None
    assert provider.resource.attributes[PROJECT_NAME] == DEFAULT_PROJECT_NAME
    assert provider.resource.attributes["service.name"] == DEFAULT_SERVICE_NAME

    with get_tracer(provider).start_as_current_span("foundation-test"):
        pass

    shutdown_tracing(provider)

    assert [span.name for span in exporter.get_finished_spans()] == [
        "foundation-test"
    ]
    assert exporter.shutdown_count == 1


def test_repeated_app_lifespans_own_independent_non_global_providers() -> None:
    global_provider = trace.get_tracer_provider()
    exporters: list[RecordingExporter] = []
    providers = []

    def tracing_factory():
        exporter = RecordingExporter()
        provider = configure_tracing(span_exporter=exporter)
        exporters.append(exporter)
        providers.append(provider)
        return provider

    for _ in range(2):
        app = create_app(tracing_factory=tracing_factory)
        with TestClient(app):
            assert app.state.tracer_provider is providers[-1]

    assert providers[0] is not providers[1]
    assert trace.get_tracer_provider() is global_provider
    assert [exporter.shutdown_count for exporter in exporters] == [1, 1]
