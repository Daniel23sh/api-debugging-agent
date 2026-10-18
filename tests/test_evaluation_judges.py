import asyncio
import json
import re
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from openinference.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from agent_service.observability import (
    configure_openai_instrumentation,
    configure_tracing,
    get_tracer,
    shutdown_openai_instrumentation,
    shutdown_tracing,
)
from evaluation.contracts import (
    EvaluatorName,
    GroundingLabel,
    QualityLabel,
    RootCauseLabel,
    SelectionLabel,
)
from evaluation.judges import (
    JudgeAnswer,
    JudgeFailure,
    OpenAIJudgeProvider,
    request_judgment,
)


async def request(judge, **overrides):
    args = {
        "evaluator": EvaluatorName.ROOT_CAUSE,
        "rubric": "Compare causal mechanisms",
        "data": {"actual": "quantity validation"},
        "label_type": RootCauseLabel,
        "evidence_ids": set(),
    }
    args.update(overrides)
    return await request_judgment(judge, **args)


def sdk_response(answer):
    return {
        "id": "resp_judge",
        "object": "response",
        "created_at": 0,
        "model": "judge-test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": "msg_judge",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(answer),
                        "annotations": [],
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label_type", [RootCauseLabel, GroundingLabel, QualityLabel, SelectionLabel]
)
async def test_actual_http_schema_name_is_api_compatible(label_type):
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", body["text"]["format"]["name"])
        return httpx.Response(
            200,
            json=sdk_response(
                {
                    "label": next(iter(label_type)).value,
                    "explanation": "Supported",
                    "evidence_refs": [],
                }
            ),
        )

    async with AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        result = await request(
            OpenAIJudgeProvider(client=sdk, model="gpt-6.1-sol"), label_type=label_type
        )
    assert not isinstance(result, JudgeFailure)
    assert len(calls) == 1 and calls[0]["model"] == "gpt-6.1-sol"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_count", [1, 2])
async def test_one_structured_correction_only(invalid_count):
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        answer = {
            "label": "NOT_A_LABEL" if len(calls) <= invalid_count else "CORRECT",
            "explanation": "Same causal mechanism",
            "evidence_refs": [],
        }
        return httpx.Response(200, json=sdk_response(answer))

    async with AsyncOpenAI(
        api_key="test",
        max_retries=5,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        result = await request(OpenAIJudgeProvider(client=sdk, model="judge-test"))
    assert len(calls) == 2
    assert all(call["store"] is False for call in calls)
    assert "preceding output was invalid" in calls[1]["instructions"]
    if invalid_count == 1:
        assert result.label is RootCauseLabel.CORRECT
    else:
        assert result.error_type == "JUDGE_OUTPUT_VALIDATION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 500])
async def test_http_error_has_no_retry_and_private_text_never_exports(status):
    marker = "PRIVATE_JUDGE_ERROR_MARKER_DO_NOT_EXPORT"
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"error": {"message": marker}})

    exporter = InMemorySpanExporter()
    provider = configure_tracing(span_exporter=exporter)
    instrumentor = configure_openai_instrumentation(provider)
    try:
        async with AsyncOpenAI(
            api_key="test",
            max_retries=5,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as sdk:
            with get_tracer(provider).start_as_current_span("evaluation") as parent:
                parent_id = parent.get_span_context().span_id
                result = await request(
                    OpenAIJudgeProvider(client=sdk, model="judge-test")
                )
        provider.force_flush()
        spans = exporter.get_finished_spans()
        llm = [
            span
            for span in spans
            if span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND) == "LLM"
        ]
        assert len(calls) == len(llm) == 1
        assert result.error_type == "JUDGE_API_ERROR"
        assert marker not in result.model_dump_json()
        assert llm[0].parent.span_id == parent_id
        assert llm[0].status.status_code is StatusCode.ERROR
        assert any(event.attributes.get("exception.type") for event in llm[0].events)
        for span in spans:
            exported = str(span.attributes) + str(span.status.description)
            exported += str([dict(event.attributes) for event in span.events])
            assert marker not in exported
    finally:
        shutdown_openai_instrumentation(instrumentor)
        shutdown_tracing(provider)


@pytest.mark.asyncio
async def test_successful_judge_io_hidden_from_automatic_trace():
    marker = "PRIVATE_JUDGE_INPUT_OUTPUT"
    exporter = InMemorySpanExporter()
    provider = configure_tracing(span_exporter=exporter)
    instrumentor = configure_openai_instrumentation(provider)
    try:
        async with AsyncOpenAI(
            api_key="test",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        200,
                        json=sdk_response(
                            {
                                "label": "CORRECT",
                                "explanation": marker,
                                "evidence_refs": [],
                            }
                        ),
                    )
                )
            ),
        ) as sdk:
            result = await request(
                OpenAIJudgeProvider(client=sdk, model="judge-test"),
                data={"actual": marker},
            )
        assert result.explanation == marker
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert marker not in str(spans[0].attributes)
        assert marker not in str(spans[0].events)
    finally:
        shutdown_openai_instrumentation(instrumentor)
        shutdown_tracing(provider)


@pytest.mark.asyncio
async def test_sdk_transport_timeout_is_not_retried():
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("PRIVATE_TIMEOUT_CONTENT", request=req)

    async with AsyncOpenAI(
        api_key="test",
        max_retries=5,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        result = await request(OpenAIJudgeProvider(client=sdk, model="judge-test"))
    assert len(calls) == 1
    assert result.error_type == "JUDGE_TIMEOUT"
    assert "PRIVATE" not in result.model_dump_json()


class FakeJudge:
    def __init__(self, answer=None):
        self.calls = 0
        self.answer = answer or JudgeAnswer[RootCauseLabel](
            label="CORRECT", explanation="Supported", evidence_refs=[]
        )

    async def judge(self, request, response_type):
        self.calls += 1
        return self.answer


@pytest.mark.asyncio
async def test_oversized_input_is_rejected_without_truncation_or_call():
    judge = FakeJudge()
    result = await request(judge, data={"actual": "x" * 64_000})
    assert result.error_type == "INPUT_TOO_LARGE"
    assert judge.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("refs", [["future_observation"], []])
async def test_evidence_references_are_validated(refs):
    judge = FakeJudge(
        JudgeAnswer[RootCauseLabel](
            label="CORRECT",
            explanation="Supported",
            evidence_refs=refs,
        )
    )
    result = await request(judge, evidence_ids={"o1"}, require_evidence=True)
    assert result.error_type == "JUDGE_INVALID_EVIDENCE_REFERENCE"


@pytest.mark.asyncio
async def test_timeout_is_safe_and_external_cancellation_propagates(monkeypatch):
    class HangingJudge:
        async def judge(self, *args):
            await asyncio.Event().wait()

    monkeypatch.setattr("evaluation.judges.JUDGE_TIMEOUT_SECONDS", 0.001)
    result = await request(HangingJudge())
    assert result.error_type == "JUDGE_TIMEOUT"
    monkeypatch.setattr("evaluation.judges.JUDGE_TIMEOUT_SECONDS", 30)
    task = asyncio.create_task(request(HangingJudge()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_model_must_be_explicit(monkeypatch):
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "agent-model")
    with pytest.raises(ValueError, match="EVAL_JUDGE_MODEL"):
        OpenAIJudgeProvider()


@pytest.mark.asyncio
async def test_owned_client_closed_in_context_only(monkeypatch):
    class Client:
        closed = False

        async def close(self):
            self.closed = True

    owned = Client()
    monkeypatch.setattr("evaluation.judges.AsyncOpenAI", lambda **kw: owned)
    async with OpenAIJudgeProvider(model="explicit"):
        assert not owned.closed
    assert owned.closed
    injected = Client()
    async with OpenAIJudgeProvider(model="explicit", client=injected):
        pass
    assert not injected.closed


@pytest.mark.asyncio
async def test_untrusted_provider_failure_is_classified_without_exception_text():
    class BadJudge:
        async def judge(self, *args):
            raise RuntimeError("PRIVATE_PROVIDER_ERROR")

    result = await request(BadJudge())
    assert isinstance(result, JudgeFailure)
    assert result.error_type == "JUDGE_PROVIDER_ERROR"
    assert "PRIVATE" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_overlong_explanation_is_rejected():
    result = await request(
        FakeJudge(
            SimpleNamespace(
                model_dump=lambda: {
                    "label": "CORRECT",
                    "explanation": "x" * 501,
                    "evidence_refs": [],
                }
            )
        )
    )
    assert result.error_type == "JUDGE_OUTPUT_VALIDATION_ERROR"
