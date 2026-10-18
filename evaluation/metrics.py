"""Observed agent runtime metadata; judge calls are outside the run recorder."""

from collections import Counter

from pydantic import Field

from agent_service.agent.schemas import DiagnosisStatus
from evaluation.contracts import Contract, EvaluationRun, RunError, TokenUsage


class SystemMetrics(Contract):
    latency_seconds: float | None = Field(default=None, ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    tool_calls: int = Field(ge=0)
    final_status: DiagnosisStatus | None = None
    token_usage: TokenUsage | None = None
    errors: list[RunError]
    error_counts: dict[str, int]


def collect_system_metrics(run: EvaluationRun) -> SystemMetrics:
    usage = None
    if run.model_calls is not None:
        totals = {}
        for field in TokenUsage.model_fields:
            values = [
                getattr(call.usage, field) if call.usage is not None else None
                for call in run.model_calls
            ]
            totals[field] = (
                sum(values) if all(value is not None for value in values) else None
            )
        if totals["input_tokens"] is None or totals["output_tokens"] is None:
            totals["total_tokens"] = None
        # Cached/reasoning values are subsets, never added to total_tokens.
        usage = TokenUsage(**totals)
    return SystemMetrics(
        latency_seconds=run.latency_seconds,
        model_calls=len(run.model_calls) if run.model_calls is not None else None,
        tool_calls=len(run.tool_attempts),
        final_status=run.diagnosis.status if run.diagnosis is not None else None,
        token_usage=usage,
        errors=[item.model_copy(deep=True) for item in run.errors],
        error_counts=dict(Counter(item.error_type for item in run.errors)),
    )
